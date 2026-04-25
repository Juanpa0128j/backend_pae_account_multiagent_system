import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database import ProcessStatus
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
            "Configure company tax profile via POST /api/v1/settings/company/{nit}/setup and retry.",
        )

    if "no staged transactions" in msg:
        return (
            "business_precondition",
            "NO_STAGED_TRANSACTIONS",
            "Run ingest first and ensure transactions_pending contains rows for this ingest_id.",
        )

    if "missing nit_receptor" in msg:
        return (
            "business_precondition",
            "MISSING_NIT_RECEPTOR",
            "Ensure staged transactions contain nit_receptor before processing.",
        )

    if "audit_blocker" in msg or "unfixable audit blockers" in msg:
        return (
            "audit_blocker",
            "AUDIT_BLOCKER",
            "Review auditor findings and correct blocking accounting issues before retrying.",
        )

    return (
        "system_error",
        "PROCESS_EXECUTION_ERROR",
        "Review process job logs and retry.",
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
                "message": (
                    "Cannot start process: no staged transactions found for this ingest_id. "
                    "Run ingest successfully before accounting processing."
                ),
                "remediation": "Run POST /api/v1/ingest/upload and verify transactions before retrying /process/accounting.",
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
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "business_precondition",
                "error_code": "MISSING_NIT_RECEPTOR",
                "message": "Cannot start process: staged transactions are missing nit_receptor.",
                "remediation": "Fix extraction/staging data so nit_receptor is present, then retry processing.",
            },
        )

    company_settings = db_service.get_company_settings(db, nit_receptor)
    if not company_settings:
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "business_precondition",
                "error_code": "MISSING_COMPANY_SETTINGS",
                "message": (
                    f"Cannot start process: missing company tax settings for NIT {nit_receptor}."
                ),
                "remediation": f"Run POST /api/v1/settings/company/{nit_receptor}/setup and retry.",
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
    )


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
                "message": (
                    f"Process job {process_id} is still being processed "
                    f"(current status: {process_job.status.value}). "
                    f"Poll /api/v1/process/status/{process_id} for updates."
                ),
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
