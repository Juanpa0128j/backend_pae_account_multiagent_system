import logging
import tempfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.agents.graph import invoke_ingest_pipeline
from app.core.auth import CurrentUser, get_current_user
from app.core.config import get_settings
from app.core.database import INGEST_PIPELINE_SEMAPHORE, SessionLocal, get_db
from app.models.database import IngestJob, IngestStatus
from app.models.document_types import (
    DocumentType,
    ParserMode,
    get_document_type_label,
    get_pathway,
    list_via_a_document_type_options,
)
from app.models.schemas import (
    ClassificationReviewUpdateRequest,
    IngestDetailResponse,
    IngestResponse,
    MergeIngestRequest,
    PeriodReviewUpdateRequest,
)
from app.services.ingest_matcher import find_merge_candidates
from app.models.trace import PipelineTrace
from app.services import db_service
from app.services.nit_utils import normalize_nit
from app.workflows.dispatch import dispatch_ingest_start

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
    temp_file_paths: list[str],
    ingest_id: str,
    company_nit: Optional[str] = None,
    parser_mode: Optional[str] = None,
    multi_file_mode: str = "pages",
):
    logger.info(
        f"Queueing background agent for: {ingest_id} (company_nit={company_nit})"
    )
    # Limit concurrent ingest pipelines to avoid exhausting the Supabase
    # connection pool. Uploads queue here instead of racing for connections.
    with INGEST_PIPELINE_SEMAPHORE:
        logger.info(f"Acquired ingest pipeline slot for: {ingest_id}")
        _run_ingest_pipeline(
            temp_file_paths, ingest_id, company_nit, parser_mode, multi_file_mode
        )


def _run_ingest_pipeline(
    temp_file_paths: list[str],
    ingest_id: str,
    company_nit: Optional[str] = None,
    parser_mode: Optional[str] = None,
    multi_file_mode: str = "pages",
):
    initial: dict = {"ingest_id": ingest_id}
    if company_nit:
        initial["company_nit"] = company_nit
    if parser_mode:
        initial["parser_mode"] = parser_mode
    initial["multi_file_mode"] = multi_file_mode
    try:
        result = invoke_ingest_pipeline(
            temp_file_paths[0],
            initial_state=initial,
            file_paths=temp_file_paths,
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
            for path in temp_file_paths:
                Path(path).unlink(missing_ok=True)


_LOW_CONFIDENCE_THRESHOLD = 0.85


def _build_period_review(db: Session, job: IngestJob) -> Optional[dict]:
    """Decide whether the accountant should re-check the extracted period.

    Returns a ``PeriodReviewResponse``-shaped dict when human review is
    advisable, ``None`` otherwise. Only fires for Vía B doc types that landed
    in ``financial_statements`` — Vía A documents don't anchor reporting
    periods the same way.

    Heuristics (any one triggers review):
      * LLM extraction confidence below ``_LOW_CONFIDENCE_THRESHOLD``.
      * Period start equals period end (collapsed range — fine for balance_general
        snapshots, suspicious for estado_resultados / libro_auxiliar that need a
        proper range).
      * Frequency column is NULL or the value came from span inference rather
        than the LLM-extracted ``periodicidad`` field.
      * Annual statement (high-value for derivation): always confirm.
    """
    from app.models.database import FinancialStatement  # noqa: PLC0415
    from app.services.financial_statement_service import (  # noqa: PLC0415
        infer_frequency,
        normalize_periodicidad,
    )

    stmt = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.ingest_id == job.id)
        .order_by(FinancialStatement.created_at.desc())
        .first()
    )
    if stmt is None:
        return None

    # Once the contador has explicitly confirmed the period via the HITL
    # editor, stop nagging — even on annual statements (which would otherwise
    # always trip the ``annual_high_value`` heuristic).
    if isinstance(stmt.data, dict) and stmt.data.get("period_reviewed_at"):
        return None

    confidence = (
        float(job.classification_confidence)
        if job.classification_confidence is not None
        else None
    )
    extracted_periodicidad = normalize_periodicidad(
        (stmt.data or {}).get("periodicidad")
    )
    inferred_frequency = infer_frequency(stmt.period_start, stmt.period_end)
    frequency_from_span = (
        extracted_periodicidad is None and inferred_frequency is not None
    )

    reasons: list[str] = []
    if confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD:
        reasons.append("low_confidence")
    if (
        stmt.period_start
        and stmt.period_end
        and stmt.period_start == stmt.period_end
        and stmt.statement_type in ("estado_resultados", "libro_auxiliar")
    ):
        reasons.append("collapsed_range")
    if frequency_from_span:
        reasons.append("span_inferred")
    # Annual is high-leverage (it drives derivation). Always confirm so the
    # contador signs off before NIC 7 flows from this row.
    if stmt.frequency == "annual" or inferred_frequency == "annual":
        reasons.append("annual_high_value")

    if not reasons:
        return None

    return {
        "extracted_period_start": (
            stmt.period_start.date().isoformat() if stmt.period_start else None
        ),
        "extracted_period_end": (
            stmt.period_end.date().isoformat() if stmt.period_end else None
        ),
        "extracted_periodicidad": stmt.frequency or inferred_frequency,
        "extraction_confidence": confidence,
        "inferred_from_span": frequency_from_span,
        "requires_review": True,
        "review_reason": reasons[0],
    }


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
                "source_file": tx.source_file,
            }
        )

    # Reconcile stale ingest states: if transactions are already staged but the
    # job is still pending/processing, mark it as completed so clients can
    # advance to the accounting phase.
    from datetime import datetime, timedelta, timezone

    created_at = job.created_at
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    is_stale = created_at and (datetime.now(timezone.utc) - created_at) > timedelta(
        seconds=60
    )
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

    # ── Period HITL ─────────────────────────────────────────────────────────
    # Only Vía B uploads land in financial_statements with a period — surface
    # the period editor when the LLM extraction was shaky or the periodicity
    # was inferred from the span rather than read off the document. The
    # accountant edits via PATCH /ingest/{id}/period below.
    period_review = _build_period_review(db, job)

    trace_url = (
        f"{base_url.rstrip('/')}/api/v1/ingest/{job.id}/trace" if base_url else None
    )

    return {
        "ingest_id": job.id,
        "file_name": job.file_name,
        "status": job.status.value if job.status else "unknown",
        "document_type": job.document_type,
        "pathway": job.pathway,
        "parser_mode": job.parser_mode,
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
        "period_review": period_review,
        "file_names": job.file_names or [],
        "multi_file_mode": job.multi_file_mode,
        "current_file_index": job.current_file_index,
    }


@router.post(
    "/upload", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_file(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    company_nit: Optional[str] = Form(
        None,
        description="Company NIT to associate with this document. If omitted, the NIT is auto-detected from the document content.",
    ),
    doc_type: Optional[str] = Form(
        None,
        description="Pre-confirmed document type (e.g. 'balance_general'). When provided, classification review is skipped. Use for Vía B uploads where the user explicitly selects the document type.",
    ),
    parser_mode: Optional[str] = Form(
        "fast",
        description="LlamaParse extraction quality mode: fast, standard, premium, gpt4o, agentic, agentic_plus.",
    ),
    multi_file_mode: Literal["pages", "documents"] = Form(
        "pages",
        description="'pages' = all files are pages of one document | 'documents' = each file is an independent document.",
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Upload and process one or more PDF/Excel/XML/image files (pages of a single document).
    Returns 202 Accepted immediately.

    Optionally pass `company_nit` as a form field to explicitly associate the document
    with a company, overriding the NIT auto-detected from the document content.
    Pass `doc_type` to pre-confirm the document type and skip the classification review step.
    """
    if not files:
        raise HTTPException(
            status_code=422,
            detail="No files provided.",
            headers={"error_code": "NO_FILES"},
        )

    # Validate file types
    for f in files:
        if not f.filename.lower().endswith(
            (".pdf", ".xlsx", ".xml", ".jpg", ".jpeg", ".png")
        ):
            raise HTTPException(
                status_code=422,
                detail="Tipo de archivo no soportado. Formatos aceptados: PDF, Excel, XML, JPG, PNG",
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

        try:
            validated_mode = (
                ParserMode(parser_mode).value if parser_mode else ParserMode.FAST.value
            )
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Modo de extracción '{parser_mode}' no válido. Opciones: fast, standard, premium, gpt4o, agentic, agentic_plus",
            )

        # Magic-byte check: reject obviously wrong/corrupt files early
        _MAGIC: dict[str, bytes] = {
            ".pdf": b"%PDF",
            ".xlsx": b"PK\x03\x04",  # ZIP-based Office Open XML
            ".xml": b"<?xml",
            ".jpg": b"\xff\xd8\xff",
            ".jpeg": b"\xff\xd8\xff",
            ".png": b"\x89PNG",
        }

        temp_file_paths: list[str] = []
        for f in files:
            file_content = await f.read()

            # Validate file is not empty
            if not file_content:
                raise HTTPException(
                    status_code=422, detail=f"El archivo {f.filename} está vacío"
                )

            _ext = Path(f.filename).suffix.lower()
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

            temp_path = save_temp_file(file_content, f.filename)
            logger.info(f"Saved uploaded file to: {temp_path}")
            temp_file_paths.append(temp_path)

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

        settings = get_settings()
        fanout_enabled = (
            multi_file_mode == "documents"
            and settings.workflow_engine == "inngest"
            and len(files) > 1
        )

        if fanout_enabled:
            jobs_created: list[IngestJob] = []
            per_file_results: list[dict] = []
            for f, temp_path in zip(files, temp_file_paths):
                job = db_service.create_ingest_job(
                    db,
                    f.filename,
                    temp_path,
                    company_nit=normalized_company_nit,
                    document_type=confirmed_doc_type,
                    pathway=confirmed_pathway,
                    classification_confirmed=True if confirmed_doc_type else None,
                    parser_mode=validated_mode,
                    created_by=str(current_user.id),
                    file_names=[f.filename],
                    multi_file_mode="pages",
                )
                jobs_created.append(job)
                try:
                    await dispatch_ingest_start(
                        ingest_id=str(job.id),
                        temp_file_paths=[temp_path],
                        company_nit=normalized_company_nit,
                        parser_mode=validated_mode,
                        multi_file_mode="pages",
                    )
                    per_file_results.append(
                        {
                            "ingest_id": str(job.id),
                            "filename": f.filename,
                            "dispatch_ok": True,
                            "error": None,
                        }
                    )
                except Exception as dispatch_err:
                    logger.warning(
                        "Failed to dispatch ingest %s to Inngest: %s",
                        job.id,
                        dispatch_err,
                    )
                    db_service.update_ingest_job(
                        db,
                        str(job.id),
                        IngestStatus.FAILED,
                        extraction_errors=[
                            "No se pudo encolar el documento para procesamiento. Reintenta la subida.",
                        ],
                    )
                    per_file_results.append(
                        {
                            "ingest_id": str(job.id),
                            "filename": f.filename,
                            "dispatch_ok": False,
                            "error": str(dispatch_err),
                        }
                    )
                    continue
            if normalized_company_nit and confirmed_pathway:
                db_service.set_company_locked_pathway(
                    db, normalized_company_nit, confirmed_pathway
                )
            first = jobs_created[0]
            return IngestResponse(
                message=f"Uploaded {len(jobs_created)} documents for parallel processing",
                ingest_id=str(first.id),
                status=first.status.value,
                file_name=first.file_name,
                ingest_ids=[str(job.id) for job in jobs_created],
                created_at=first.created_at,
                extracted_transactions=0,
                raw_preview=None,
                per_file_results=per_file_results,
            )

        first_file = files[0]
        ingest_job = db_service.create_ingest_job(
            db,
            first_file.filename,
            temp_file_paths[0],
            company_nit=normalized_company_nit,
            document_type=confirmed_doc_type,
            pathway=confirmed_pathway,
            classification_confirmed=True if confirmed_doc_type else None,
            parser_mode=validated_mode,
            created_by=str(current_user.id),
            file_names=[f.filename for f in files],
            multi_file_mode=multi_file_mode,
        )
        logger.info(f"Created IngestJob: {ingest_job.id}")

        # Lock the company to this pathway on first upload (when pathway is known).
        if normalized_company_nit and confirmed_pathway:
            db_service.set_company_locked_pathway(
                db, normalized_company_nit, confirmed_pathway
            )

        background_tasks.add_task(
            process_ingest_background,
            temp_file_paths,
            str(ingest_job.id),
            normalized_company_nit,
            validated_mode,
            multi_file_mode,
        )

        return IngestResponse(
            message="File uploaded successfully and queued for processing",
            ingest_id=str(ingest_job.id),
            status=ingest_job.status.value,
            file_name=first_file.filename,
            ingest_ids=[str(ingest_job.id)],
            created_at=ingest_job.created_at,
            extracted_transactions=0,
            raw_preview=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        first_name = files[0].filename if files else "unknown"
        logger.error(f"Error queueing file {first_name}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error queueing file: {str(e)}")


@router.get("/merge-suggestions")
async def get_merge_suggestions(
    company_nit: str = Query(..., description="Company NIT"),
    time_window_minutes: int = Query(5, ge=1, le=60),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Returns groups of ingest jobs that look like pages of the same document."""
    normalized_company_nit = normalize_nit(company_nit)
    suggestions = find_merge_candidates(
        db, normalized_company_nit, time_window_minutes=time_window_minutes
    )
    return {"suggestions": suggestions}


@router.get("/{ingest_id}", response_model=IngestDetailResponse)
async def get_ingest_status(
    ingest_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
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
    current_user: CurrentUser = Depends(get_current_user),
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
        raise HTTPException(
            status_code=500, detail="Error al actualizar el trabajo de ingesta"
        )

    if payload.confirmed:
        if not job.file_path:
            raise HTTPException(
                status_code=422,
                detail="El trabajo de ingesta no tiene ruta de archivo para reanudar",
            )
        # Reconstruct all file paths from stored file_names; fall back to single file_path.
        temp_dir = Path(tempfile.gettempdir()) / "pae_uploads"
        if job.file_names and len(job.file_names) > 1:
            all_file_paths = [str(temp_dir / name) for name in job.file_names]
        else:
            all_file_paths = [job.file_path]
        background_tasks.add_task(
            process_ingest_background,
            all_file_paths,
            str(job.id),
            job.company_nit,
            job.parser_mode,
            job.multi_file_mode or "pages",
        )

    refreshed = db_service.get_ingest_job(db, ingest_id)
    if not refreshed:
        raise HTTPException(
            status_code=500, detail="Error al refrescar el trabajo de ingesta"
        )
    return _build_ingest_detail_response(db, refreshed, base_url=str(request.base_url))


@router.patch(
    "/{ingest_id}/period",
    response_model=IngestDetailResponse,
    status_code=status.HTTP_200_OK,
)
async def update_ingest_period(
    ingest_id: str,
    body: PeriodReviewUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """HITL override of the period extracted by the LLM.

    Updates the ``FinancialStatement`` row associated with the ingest:
    ``period_start``, ``period_end``, and ``frequency`` (normalised from
    ``periodicidad``). Records an audit log so the override is traceable
    (Ley 43/1990 — the contador is responsible for the final figure).

    Refuses ``period_end < period_start`` with 400.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from sqlalchemy.orm.attributes import flag_modified  # noqa: PLC0415

    from app.models.database import FinancialStatement  # noqa: PLC0415
    from app.services.financial_statement_service import (  # noqa: PLC0415
        infer_frequency,
        normalize_periodicidad,
    )

    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo de ingesta no encontrado")

    try:
        new_start = datetime.fromisoformat(body.period_start)
        new_end = datetime.fromisoformat(body.period_end)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="period_start y period_end deben ser ISO YYYY-MM-DD",
        )
    if new_end.tzinfo is None:
        new_end = new_end.replace(tzinfo=timezone.utc)
    if new_start.tzinfo is None:
        new_start = new_start.replace(tzinfo=timezone.utc)
    if new_end < new_start:
        raise HTTPException(
            status_code=400,
            detail="period_end no puede ser anterior a period_start",
        )

    stmt = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.ingest_id == ingest_id)
        .order_by(FinancialStatement.created_at.desc())
        .first()
    )
    if stmt is None:
        raise HTTPException(
            status_code=404,
            detail="No hay un estado financiero asociado a este ingest para editar período",
        )

    # Normalise frequency: prefer the explicit user choice; fall back to span.
    new_frequency = normalize_periodicidad(body.periodicidad) or infer_frequency(
        new_start, new_end
    )

    prev = {
        "period_start": stmt.period_start.isoformat() if stmt.period_start else None,
        "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
        "frequency": stmt.frequency,
    }
    stmt.period_start = new_start
    stmt.period_end = new_end
    stmt.frequency = new_frequency
    # Keep the JSONB payload in sync so downstream readers (chat, reports)
    # see the same period without a database join. ``period_reviewed_at`` is
    # the sentinel ``_build_period_review`` checks to stop re-prompting.
    if isinstance(stmt.data, dict):
        stmt.data = {
            **stmt.data,
            "periodo_inicio": new_start.date().isoformat(),
            "periodo_fin": new_end.date().isoformat(),
            "periodicidad": body.periodicidad,
            "period_reviewed_at": datetime.now(timezone.utc).isoformat(),
            "period_reviewed_by": getattr(current_user, "email", None),
        }
        # JSONB mutation tracking — flag explicitly so the UPDATE fires even
        # if SQLAlchemy's attribute instrumentation misses the dict swap.
        flag_modified(stmt, "data")
    db.add(stmt)

    # Audit trail — Ley 43/1990 traceability.
    db_service.create_audit_log(
        db,
        action="period_review_override",
        entity_id=stmt.id,
        entity_type="financial_statement",
        company_nit=stmt.entity_nit,
        created_by=getattr(current_user, "email", None),
        details={
            "ingest_id": ingest_id,
            "previous": prev,
            "new": {
                "period_start": new_start.date().isoformat(),
                "period_end": new_end.date().isoformat(),
                "frequency": new_frequency,
                "periodicidad": body.periodicidad,
            },
        },
        commit=False,
    )

    db.commit()
    db.refresh(job)
    return _build_ingest_detail_response(db, job, base_url=str(request.base_url))


@router.patch(
    "/{ingest_id}/cancel",
    response_model=IngestDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_ingest(
    ingest_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo de ingesta no encontrado")

    current_status = job.status
    if isinstance(current_status, str):
        current_status = IngestStatus(current_status)

    if current_status == IngestStatus.CANCELLED:
        raise HTTPException(
            status_code=409, detail="El trabajo de ingesta ya fue cancelado"
        )

    if current_status in (IngestStatus.COMPLETED, IngestStatus.FAILED):
        raise HTTPException(
            status_code=409, detail="No se puede cancelar un trabajo que ya terminó"
        )

    db_service.update_ingest_job(db, ingest_id, IngestStatus.CANCELLED)

    # Clean up temp file if present
    if job.file_path:
        try:
            Path(job.file_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete temp file %s", job.file_path)

    refreshed = db_service.get_ingest_job(db, ingest_id)
    return _build_ingest_detail_response(db, refreshed, base_url=str(request.base_url))


@router.patch("/{ingest_id}/merge", response_model=IngestDetailResponse)
async def merge_ingest_jobs(
    ingest_id: str,
    request: MergeIngestRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Merge source ingest job into target ingest job.

    - Concatenates raw_data from both TransactionPending rows
    - Marks source job as CANCELLED
    """
    if ingest_id == request.source_ingest_id:
        raise HTTPException(
            status_code=400,
            detail="Source and target ingest jobs must be different",
        )

    target = db_service.get_ingest_job(db, ingest_id)
    if not target:
        raise HTTPException(
            status_code=404, detail=f"Target ingest job {ingest_id} not found"
        )

    source = db_service.get_ingest_job(db, request.source_ingest_id)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=f"Source ingest job {request.source_ingest_id} not found",
        )

    if target.company_nit != source.company_nit:
        raise HTTPException(
            status_code=400, detail="Ingest jobs belong to different companies"
        )

    if target.status in (IngestStatus.CANCELLED, IngestStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail="Target ingest job is already cancelled or failed",
        )

    if source.status in (IngestStatus.CANCELLED, IngestStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail="Source ingest job is already cancelled or failed",
        )

    # Merge raw_data from TransactionPending rows
    target_txns = db_service.get_transactions_by_ingest(db, ingest_id)
    source_txns = db_service.get_transactions_by_ingest(db, request.source_ingest_id)

    if target_txns and source_txns:
        source_raw_list: list = []
        for txn in source_txns:
            if txn.raw_data is None:
                continue
            # Flatten when the source was already merged previously (raw_data is
            # a list) — otherwise we'd build nested lists across multiple merges.
            if isinstance(txn.raw_data, list):
                source_raw_list.extend(txn.raw_data)
            else:
                source_raw_list.append(txn.raw_data)

        if source_raw_list:
            target_txn = target_txns[0]
            if target_txn.raw_data is None:
                if len(source_raw_list) == 1:
                    target_txn.raw_data = source_raw_list[0]
                else:
                    target_txn.raw_data = source_raw_list
            elif isinstance(target_txn.raw_data, list):
                target_txn.raw_data = target_txn.raw_data + source_raw_list
            else:
                target_txn.raw_data = [target_txn.raw_data] + source_raw_list
            db.commit()
            db.refresh(target_txn)

    # Mark source as cancelled
    existing_errors = source.extraction_errors or []
    db_service.update_ingest_job(
        db,
        request.source_ingest_id,
        IngestStatus.CANCELLED,
        extraction_errors=existing_errors + [f"Merged into {ingest_id}"],
    )

    refreshed = db_service.get_ingest_job(db, ingest_id)
    return _build_ingest_detail_response(db, refreshed)


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
