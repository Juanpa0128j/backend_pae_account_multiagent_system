import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database import ProcessJob, ProcessStatus
from app.models.schemas import (
    ProcessResponse,
    ProcessStatusResponse,
    ProcessResultResponse,
)
from app.models.trace import PipelineTrace
from app.services import db_service, jobs
from app.services.pipeline_trace_service import build_trace

router = APIRouter()


def _classify_process_error(
    error_message: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Classify process failures for API consumers.

    Accepts either:
    - Structured JSON payloads in ``error_message`` with keys
      ``error_category``, ``error_code`` and ``remediation``.
    - Legacy free-form text, classified via substring matching.
    """
    if not error_message:
        return None, None, None

    stripped = error_message.lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                category = payload.get("error_category")
                code = payload.get("error_code")
                remediation = payload.get("remediation")
                if category is not None or code is not None or remediation is not None:
                    return category, code, remediation
        except Exception:
            # Fall back to legacy text classification.
            pass

    msg = error_message.lower()

    if "missing company tax settings" in msg:
        return (
            "business_precondition",
            "MISSING_COMPANY_SETTINGS",
            "Configure el perfil tributario de la empresa y vuelva a intentarlo.",
        )

    if "no staged transactions" in msg:
        return (
            "business_precondition",
            "NO_STAGED_TRANSACTIONS",
            "Ejecute primero la ingesta y verifique que existan transacciones pendientes antes de procesar.",
        )

    if "missing nit_receptor" in msg:
        return (
            "business_precondition",
            "MISSING_NIT_RECEPTOR",
            "Las transacciones no contienen NIT receptor. Suba el documento nuevamente seleccionando una empresa.",
        )

    if "audit_blocker" in msg or "unfixable audit blockers" in msg:
        return (
            "audit_blocker",
            "AUDIT_BLOCKER",
            "Revise los hallazgos del auditor y corrija los problemas contables bloqueantes antes de reintentar.",
        )

    if (
        "puc validation failed" in msg
        or "puc_not_found" in msg
        or "missing codes" in msg
    ):
        return (
            "validation_error",
            "PUC_CODES_NOT_FOUND",
            "Los códigos PUC indicados no existen en la base de datos. Corrija el documento y vuelva a cargarlo.",
        )

    return (
        "system_error",
        "PROCESS_EXECUTION_ERROR",
        "Error en la ejecución del proceso contable. Revise el documento cargado e intente nuevamente.",
    )


@router.post("/accounting/{ingest_id}", response_model=ProcessResponse)
async def process_accounting(ingest_id: str, db: Session = Depends(get_db)):
    """
    Create a process job and execute the graph asynchronously.

    Idempotent: Returns the existing ProcessJob if one is already running or queued
    for the given ingest_id. Only creates a new one if the previous one failed or was cancelled.

    Returns a process_id for polling:
    - GET /api/v1/process/status/{process_id}
    - GET /api/v1/process/result/{process_id}
    """
    ingest_job = db_service.get_ingest_job(db, ingest_id)
    if not ingest_job:
        raise HTTPException(status_code=404, detail=f"Ingest job {ingest_id} not found")

    staged = db_service.get_transactions_by_ingest(db, ingest_id)
    if not staged:
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "business_precondition",
                "error_code": "NO_STAGED_TRANSACTIONS",
                "message": "No se encontraron transacciones para procesar. Ejecute primero la ingesta del documento.",
                "remediation": "Suba el documento y verifique que la ingesta haya completado correctamente antes de reintentar.",
            },
        )

    nit_receptor = next(
        (
            getattr(tx, "nit_receptor", None)
            for tx in staged
            if getattr(tx, "nit_receptor", None)
        ),
        None,
    )
    if not nit_receptor:
        nit_receptor = ingest_job.company_nit or None
    if not nit_receptor:
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "business_precondition",
                "error_code": "MISSING_NIT_RECEPTOR",
                "message": "Las transacciones no contienen NIT receptor y no se seleccionó empresa al subir el documento.",
                "remediation": "Seleccione una empresa antes de subir el documento y vuelva a intentarlo.",
            },
        )

    company_settings = db_service.get_company_settings(db, nit_receptor)
    if not company_settings:
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "business_precondition",
                "error_code": "MISSING_COMPANY_SETTINGS",
                "message": f"No se encontró configuración tributaria para el NIT {nit_receptor}.",
                "remediation": f"Configure el perfil tributario de la empresa con NIT {nit_receptor} y vuelva a intentarlo.",
            },
        )

    # Check if an active ProcessJob already exists for this ingest_id
    existing_job = db_service.get_active_process_job_for_ingest(db, ingest_id)
    if existing_job:
        return ProcessResponse(
            message=f"Process job already exists for ingest_id: {ingest_id}",
            process_id=existing_job.id,
            status=existing_job.status.value,
        )

    # Create a new ProcessJob only if no active one exists
    process_job = db_service.create_process_job(db, ingest_id)
    await jobs.start_process_job(process_job.id)

    return ProcessResponse(
        message=f"Started accounting process for ingest_id: {ingest_id}",
        process_id=process_job.id,
        status=process_job.status.value,
    )


@router.get("/status/{process_id}", response_model=ProcessStatusResponse)
async def get_process_status(
    process_id: str, request: Request, db: Session = Depends(get_db)
):
    """Polling endpoint for async process job status and progress."""
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        raise HTTPException(
            status_code=404, detail=f"Process job {process_id} not found"
        )

    error_category, error_code, remediation = _classify_process_error(
        process_job.error_message
    )

    raw_log = process_job.agent_log or []
    has_warnings = any(
        e.get("event") in {"audit_finding", "warning", "non_fatal_error"}
        for e in raw_log
    )

    base_url = str(request.base_url).rstrip("/")
    trace_url = f"{base_url}/api/v1/process/{process_id}/trace"

    # Extract audit_review data when status is pending_audit_review
    audit_review = None
    if _enum_value(process_job.status).upper() == "PENDING_AUDIT_REVIEW":
        for entry in reversed(raw_log):
            if entry.get("event") == "audit_giveup":
                audit_review = entry.get("details")
                break

    return ProcessStatusResponse(
        process_id=process_job.id,
        status=process_job.status.value,
        current_stage=process_job.current_stage,
        current_agent=process_job.current_agent,
        progress=process_job.progress,
        error_message=process_job.error_message,
        error_category=error_category,
        error_code=error_code,
        remediation=remediation,
        agent_log=raw_log,
        created_at=(
            process_job.created_at.isoformat() if process_job.created_at else None
        ),
        started_at=(
            process_job.started_at.isoformat() if process_job.started_at else None
        ),
        completed_at=(
            process_job.completed_at.isoformat() if process_job.completed_at else None
        ),
        has_warnings=has_warnings,
        trace_url=trace_url,
        audit_review=audit_review,
    )


_ES_STATUS = {
    "queued": "en cola",
    "running": "en ejecución",
    "completed": "completado",
    "failed": "fallido",
    "cancelled": "cancelado",
    "pending_audit_review": "pendiente de revisión",
}


def _enum_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


@router.post("/{process_id}/audit-confirm", status_code=202)
async def confirm_audit_review(process_id: str, db: Session = Depends(get_db)):
    """User confirms to force-persist despite audit issues.

    Atomic: uses a guarded UPDATE that only succeeds when the row is still in
    PENDING_AUDIT_REVIEW. This prevents double-confirm from spawning two
    concurrent force-persist runs (which would write duplicate rows).
    """
    # Guarded transition PENDING_AUDIT_REVIEW → RUNNING.
    # Returns 1 row updated only if the status was still pending.
    rows_updated = (
        db.query(ProcessJob)
        .filter(
            ProcessJob.id == process_id,
            ProcessJob.status == ProcessStatus.PENDING_AUDIT_REVIEW,
        )
        .update(
            {
                ProcessJob.status: ProcessStatus.RUNNING,
                ProcessJob.current_stage: "supervisor",
                ProcessJob.current_agent: "supervisor",
                ProcessJob.progress: 10,
            },
            synchronize_session=False,
        )
    )
    db.commit()

    if rows_updated == 0:
        # Either the job doesn't exist, or it's not in pending state.
        process_job = db_service.get_process_job(db, process_id)
        if not process_job:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró el proceso {process_id}.",
            )
        es_status = _ES_STATUS.get(
            _enum_value(process_job.status).lower(),
            _enum_value(process_job.status),
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"El proceso {process_id} no está en estado pendiente de revisión "
                f"(estado actual: {es_status})."
            ),
        )

    await jobs.start_process_job(process_id, force_persist=True)
    return {
        "message": "Revisión confirmada. Reintentando persistencia.",
        "process_id": process_id,
    }


@router.get("/{process_id}/trace", response_model=PipelineTrace)
async def get_process_trace(process_id: str, db: Session = Depends(get_db)):
    """Accountant-facing trace for a process run.

    Returns a structured Spanish-language timeline of each pipeline step,
    any audit findings, and the reason auto-fix gave up (if applicable).
    """
    trace = build_trace(process_id, db)
    if trace is None:
        raise HTTPException(
            status_code=404, detail=f"Process job {process_id} not found"
        )
    return trace


@router.get("/result/{process_id}", response_model=ProcessResultResponse)
async def get_process_result(process_id: str, db: Session = Depends(get_db)):
    """Return final processed transactions for a completed process job."""
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        raise HTTPException(
            status_code=404, detail=f"Process job {process_id} not found"
        )

    if process_job.status == ProcessStatus.FAILED:
        error_category, error_code, remediation = _classify_process_error(
            process_job.error_message
        )
        status_code = 409 if error_category == "business_precondition" else 500
        return JSONResponse(
            status_code=status_code,
            content={
                "message": f"Process job {process_id} failed.",
                "process_id": process_id,
                "ingest_id": process_job.ingest_id,
                "status": process_job.status.value,
                "error_message": process_job.error_message,
                "error_category": error_category,
                "error_code": error_code,
                "remediation": remediation,
            },
        )

    if process_job.status != ProcessStatus.COMPLETED:
        return JSONResponse(
            status_code=202,
            content={
                "message": "El proceso contable aún está en curso. Por favor espera y consulta el estado nuevamente en unos segundos.",
                "process_id": process_id,
                "status": process_job.status.value,
                "error_category": None,
                "error_code": None,
                "remediation": None,
            },
        )

    transactions = db_service.get_process_result_transactions(db, process_job.ingest_id)

    return ProcessResultResponse(
        process_id=process_job.id,
        ingest_id=process_job.ingest_id,
        status=process_job.status.value,
        transactions=transactions,
        error_message=None,
        error_category=None,
        error_code=None,
        remediation=None,
    )
