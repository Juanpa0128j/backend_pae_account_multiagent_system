import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.agents.graph import invoke_ingest_pipeline
from app.core.database import INGEST_PIPELINE_SEMAPHORE, SessionLocal, get_db
from app.models.database import IngestJob, IngestStatus
from app.models.document_types import (
    DocumentType,
    get_document_type_label,
    get_pathway,
    list_via_a_document_type_options,
)
from app.models.schemas import (
    ClassificationReviewUpdateRequest,
    IngestDetailResponse,
    IngestResponse,
)
from app.models.trace import PipelineTrace
from app.services import db_service
from app.services.nit_utils import normalize_nit

logger = logging.getLogger(__name__)
router = APIRouter()


def save_temp_file(file_content: bytes, filename: str) -> str:
    """Save file to temporary directory."""
    temp_dir = Path(tempfile.gettempdir()) / "pae_uploads"
    temp_dir.mkdir(exist_ok=True)
    temp_path = temp_dir / Path(filename).name

    with open(temp_path, "wb") as f:
        f.write(file_content)

    return str(temp_path)


def process_ingest_background(
    temp_file_path: str, ingest_id: str, company_nit: Optional[str] = None
):
    logger.info(
        f"Queueing background agent for: {ingest_id} (company_nit={company_nit})"
    )
    # Limit concurrent ingest pipelines to avoid exhausting the Supabase
    # connection pool. Uploads queue here instead of racing for connections.
    with INGEST_PIPELINE_SEMAPHORE:
        logger.info(f"Acquired ingest pipeline slot for: {ingest_id}")
        _run_ingest_pipeline(temp_file_path, ingest_id, company_nit)


def _run_ingest_pipeline(
    temp_file_path: str, ingest_id: str, company_nit: Optional[str] = None
):
    initial: dict = {"ingest_id": ingest_id}
    if company_nit:
        initial["company_nit"] = company_nit
    try:
        result = invoke_ingest_pipeline(
            temp_file_path,
            initial_state=initial,
        )
        pipeline_error = None
        if isinstance(result, dict):
            pipeline_error = result.get("error")

        if pipeline_error:
            db = SessionLocal()
            try:
                db_service.update_ingest_job(
                    db,
                    ingest_id,
                    IngestStatus.FAILED,
                    extraction_errors=[
                        f"Background ingest pipeline error: {pipeline_error}"
                    ],
                )
            except Exception as status_err:
                logger.error(
                    "Failed to mark ingest %s as FAILED after pipeline error payload: %s",
                    ingest_id,
                    status_err,
                    exc_info=True,
                )
            finally:
                db.close()
        elif company_nit and isinstance(result, dict):
            # Lock the company to the pathway determined by the classifier.
            # This covers Via A uploads where doc_type was not pre-confirmed.
            pathway = result.get("pathway")
            if pathway:
                db = SessionLocal()
                try:
                    db_service.set_company_locked_pathway(db, company_nit, pathway)
                except Exception as lock_err:
                    logger.warning(
                        "Failed to set locked_pathway for company %s after ingest %s: %s",
                        company_nit,
                        ingest_id,
                        lock_err,
                    )
                finally:
                    db.close()
    except Exception as e:
        logger.error(f"Error in background ingest {ingest_id}: {e}", exc_info=True)
        # Prevent clients from waiting forever on pending_processing when the
        # background task crashes before graph-level persistence can run.
        db = SessionLocal()
        try:
            db_service.update_ingest_job(
                db,
                ingest_id,
                IngestStatus.FAILED,
                extraction_errors=[f"Background ingest error: {str(e)}"],
            )
        except Exception as status_err:
            logger.error(
                "Failed to mark ingest %s as FAILED after background exception: %s",
                ingest_id,
                status_err,
                exc_info=True,
            )
        finally:
            db.close()
    finally:
        keep_file = False
        db = SessionLocal()
        try:
            job = db_service.get_ingest_job(db, ingest_id)
            keep_file = bool(job and job.status == IngestStatus.PENDING_REVIEW)
        except Exception as status_err:
            logger.error(
                "Failed to read ingest %s for cleanup: %s",
                ingest_id,
                status_err,
                exc_info=True,
            )
        finally:
            db.close()

        if not keep_file:
            Path(temp_file_path).unlink(missing_ok=True)


def _build_ingest_detail_response(
    db: Session, job: IngestJob, base_url: Optional[str] = None
) -> dict:
    raw_txs = []
    for tx in job.transactions_pending:
        raw_txs.append(
            {
                "fecha": tx.fecha.isoformat() if tx.fecha else "",
                "nit_emisor": tx.nit_emisor or "",
                "nit_receptor": tx.nit_receptor or "",
                "total": float(tx.total) if tx.total is not None else 0.0,
                "descripcion": tx.descripcion,
                "items": tx.items if isinstance(tx.items, list) else [],
            }
        )

    # Reconcile stale ingest states: if transactions are already staged but the
    # job is still pending/processing, mark it as completed so clients can
    # advance to the accounting phase.
    from datetime import datetime, timedelta, timezone

    is_stale = job.created_at and (
        datetime.now(timezone.utc) - job.created_at
    ) > timedelta(seconds=60)
    if (
        raw_txs
        and is_stale
        and job.status in (IngestStatus.PENDING_PROCESSING, IngestStatus.PROCESSING)
        and not job.extraction_errors
    ):
        updated = db_service.update_ingest_job(db, job.id, IngestStatus.COMPLETED)
        if updated:
            job = updated

    classification_review = None
    if job.status == IngestStatus.PENDING_REVIEW:
        predicted_type = job.document_type
        predicted_label = None
        if predicted_type:
            try:
                predicted_label = get_document_type_label(DocumentType(predicted_type))
            except ValueError:
                predicted_label = predicted_type

        from app.models.document_types import _VIA_B_TYPES

        is_wrong_area = bool(
            predicted_type and predicted_type in {t.value for t in _VIA_B_TYPES}
        )

        classification_review = {
            "predicted_type": predicted_type,
            "predicted_label": predicted_label,
            "confidence": (
                float(job.classification_confidence)
                if job.classification_confidence is not None
                else None
            ),
            "available_types": list_via_a_document_type_options(),
            "wrong_upload_area": is_wrong_area,
        }

    trace_url = (
        f"{base_url.rstrip('/')}/api/v1/ingest/{job.id}/trace" if base_url else None
    )

    return {
        "ingest_id": job.id,
        "file_name": job.file_name,
        "status": job.status.value if job.status else "unknown",
        "document_type": job.document_type,
        "pathway": job.pathway,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "extraction_errors": job.extraction_errors or [],
        "raw_transactions": raw_txs,
        "error_category": "extraction_error" if job.extraction_errors else None,
        "error_code": "INGEST_ERROR" if job.extraction_errors else None,
        "remediation": (
            "El sistema no pudo procesar el documento. "
            "Verifique que el archivo esté completo y en un formato compatible (PDF, Excel, imagen), "
            "luego intente cargarlo nuevamente."
            if job.extraction_errors
            else None
        ),
        "has_warnings": False,
        "trace_url": trace_url,
        "classification_review": classification_review,
    }


@router.post(
    "/upload", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    company_nit: Optional[str] = Form(
        None,
        description="Company NIT to associate with this document. If omitted, the NIT is auto-detected from the document content.",
    ),
    doc_type: Optional[str] = Form(
        None,
        description="Pre-confirmed document type (e.g. 'balance_general'). When provided, classification review is skipped. Use for Vía B uploads where the user explicitly selects the document type.",
    ),
    db: Session = Depends(get_db),
):
    """
    Upload and process a PDF/Excel/XML/image file (receipt/invoice/scan).
    Returns 202 Accepted immediately.

    Optionally pass `company_nit` as a form field to explicitly associate the document
    with a company, overriding the NIT auto-detected from the document content.
    Pass `doc_type` to pre-confirm the document type and skip the classification review step.
    """
    # Validate file type
    if not file.filename.lower().endswith(
        (".pdf", ".xlsx", ".xml", ".jpg", ".jpeg", ".png")
    ):
        raise HTTPException(
            status_code=422,
            detail="Unsupported file type. Accepted: PDF, Excel, XML, JPG, PNG",
            headers={"error_code": "INVALID_FILE_TYPE"},
        )

    try:
        normalized_company_nit = None
        if company_nit is not None:
            try:
                normalized_company_nit = normalize_nit(company_nit)
            except ValueError as nit_err:
                raise HTTPException(
                    status_code=422, detail=f"Invalid company_nit: {nit_err}"
                )

        file_content = await file.read()

        # Validate file is not empty
        if not file_content:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")

        # Magic-byte check: reject obviously wrong/corrupt files early
        _MAGIC: dict[str, bytes] = {
            ".pdf": b"%PDF",
            ".xlsx": b"PK\x03\x04",  # ZIP-based Office Open XML
            ".xml": b"<?xml",
            ".jpg": b"\xff\xd8\xff",
            ".jpeg": b"\xff\xd8\xff",
            ".png": b"\x89PNG",
        }
        _ext = Path(file.filename).suffix.lower()
        _expected_magic = _MAGIC.get(_ext)
        if _expected_magic and not file_content[: len(_expected_magic)].startswith(
            _expected_magic
        ):
            # XML may start with a BOM or whitespace — allow those through
            stripped = file_content.lstrip()
            if _ext == ".xml" and any(
                stripped.startswith(prefix)
                for prefix in (
                    b"<?xml",
                    b"<Invoice",
                    b"<Credit",
                    b"<Debit",
                    b"\xef\xbb\xbf<?xml",
                )
            ):
                pass
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"File content does not match its extension ({_ext}). The file may be corrupt or password-protected.",
                )

        temp_file_path = save_temp_file(file_content, file.filename)
        logger.info(f"Saved uploaded file to: {temp_file_path}")

        confirmed_doc_type = None
        confirmed_pathway = None
        if doc_type is not None:
            try:
                from app.models.document_types import (
                    DocumentType,
                    _VIA_B_TYPES,
                    get_pathway,
                )

                parsed_doc_type = DocumentType(doc_type)
                if parsed_doc_type not in _VIA_B_TYPES:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"El tipo de documento '{doc_type}' pertenece a Vía A (documentos fuente). "
                            "El parámetro doc_type solo acepta tipos de Vía B: "
                            "balance_general, estado_resultados, libro_auxiliar."
                        ),
                    )
                confirmed_doc_type = parsed_doc_type.value
                confirmed_pathway = get_pathway(parsed_doc_type).value
            except HTTPException:
                raise
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"Tipo de documento '{doc_type}' no válido.",
                )

        # Vía B requires a company NIT — without it, persist will fail.
        if confirmed_pathway == "work_with_existing" and not normalized_company_nit:
            raise HTTPException(
                status_code=422,
                detail="Los documentos de Vía B requieren seleccionar una empresa (company_nit).",
            )

        # Enforce Via A / Via B mutual exclusion per company.
        # Via B uploads always have confirmed_pathway set → check directly.
        # Via A uploads without doc_type have confirmed_pathway=None (unclassified),
        # but a company locked to Via B must not accept them either.
        if normalized_company_nit:
            locked = db_service.get_company_locked_pathway(db, normalized_company_nit)
            if locked:
                conflict = (
                    confirmed_pathway is not None and locked != confirmed_pathway
                ) or (confirmed_pathway is None and locked == "work_with_existing")
                if conflict:
                    locked_label = (
                        "Vía A (documentos fuente)"
                        if locked == "build_from_scratch"
                        else "Vía B (estados financieros)"
                    )
                    incoming_label = (
                        "Vía B"
                        if confirmed_pathway == "work_with_existing"
                        else "Vía A"
                    )
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Esta empresa ya está usando {locked_label}. "
                            f"No se pueden mezclar documentos de {incoming_label} con los existentes. "
                            "Selecciona otra empresa o usa el mismo tipo de vía."
                        ),
                    )

        ingest_job = db_service.create_ingest_job(
            db,
            file.filename,
            temp_file_path,
            company_nit=normalized_company_nit,
            document_type=confirmed_doc_type,
            pathway=confirmed_pathway,
            classification_confirmed=True if confirmed_doc_type else None,
        )
        logger.info(f"Created IngestJob: {ingest_job.id}")

        # Lock the company to this pathway on first upload (when pathway is known).
        if normalized_company_nit and confirmed_pathway:
            db_service.set_company_locked_pathway(
                db, normalized_company_nit, confirmed_pathway
            )

        background_tasks.add_task(
            process_ingest_background,
            temp_file_path,
            str(ingest_job.id),
            normalized_company_nit,
        )

        return IngestResponse(
            message="File uploaded successfully and queued for processing",
            ingest_id=str(ingest_job.id),
            status=ingest_job.status.value,
            file_name=file.filename,
            created_at=ingest_job.created_at,
            extracted_transactions=0,
            raw_preview=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing file {file.filename}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error queueing file: {str(e)}")


@router.get("/{ingest_id}", response_model=IngestDetailResponse)
async def get_ingest_status(
    ingest_id: str, request: Request, db: Session = Depends(get_db)
):
    """Get the status of an ingest job."""
    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingest ID {ingest_id} not found")

    return _build_ingest_detail_response(db, job, base_url=str(request.base_url))


@router.patch("/{ingest_id}/classification", response_model=IngestDetailResponse)
async def update_ingest_classification(
    ingest_id: str,
    payload: ClassificationReviewUpdateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingest ID {ingest_id} not found")

    try:
        doc_type = DocumentType(payload.doc_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid doc_type '{payload.doc_type}'",
        )

    pathway = get_pathway(doc_type)
    target_status = (
        IngestStatus.PENDING_PROCESSING
        if payload.confirmed
        else IngestStatus.PENDING_REVIEW
    )

    updated = db_service.update_ingest_job(
        db,
        ingest_id,
        target_status,
        document_type=doc_type.value,
        pathway=pathway.value,
        classification_confirmed=payload.confirmed,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update ingest job")

    if payload.confirmed:
        if not job.file_path:
            raise HTTPException(
                status_code=422,
                detail="Ingest job has no file_path to resume",
            )
        background_tasks.add_task(
            process_ingest_background,
            job.file_path,
            str(job.id),
            job.company_nit,
        )

    refreshed = db_service.get_ingest_job(db, ingest_id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="Failed to refresh ingest job")
    return _build_ingest_detail_response(db, refreshed, base_url=str(request.base_url))


@router.get("/{ingest_id}/trace", response_model=PipelineTrace)
async def get_ingest_trace(ingest_id: str, db: Session = Depends(get_db)):
    """Accountant-facing trace for an ingest job.

    Returns 404 if the job is not found.
    Returns 409 if the job is still running (not in a terminal state).
    """
    from app.models.database import IngestStatus
    from app.services.pipeline_trace_service import build_ingest_trace

    # Check if job exists first
    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingest job {ingest_id} not found")

    # Check if job is in a terminal state
    if job.status not in (IngestStatus.COMPLETED, IngestStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "job_not_ready",
                "error_code": "INGEST_NOT_COMPLETE",
                "message": "El documento aún se está procesando. Por favor espera unos segundos y vuelve a intentarlo.",
                "remediation": "Espera a que el procesamiento termine antes de continuar.",
            },
        )

    trace = build_ingest_trace(ingest_id, db)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Ingest job {ingest_id} not found")
    return trace
