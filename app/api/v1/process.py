import logging
from datetime import datetime
from typing import Any
# type: ignore[assignment]
# SQLAlchemy instance attributes are correctly resolved at runtime.

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.graph import invoke_process_pipeline
from app.core.database import get_db
from app.models.database import ProcessStatus
from app.models.schemas import ProcessResponse, ProcessStatusResponse
from app.services import db_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@router.post("/accounting/{ingest_id}", response_model=ProcessResponse, status_code=status.HTTP_202_ACCEPTED)
async def process_accounting(ingest_id: str, db: Session = Depends(get_db)):
    """
    Trigger accounting process from staged transactions (Pipeline 2).
    """
    ingest_job = db_service.get_ingest_job(db, ingest_id)
    if not ingest_job:
        raise HTTPException(status_code=404, detail=f"Ingest ID {ingest_id} not found")

    staged = db_service.get_transactions_by_ingest(db, ingest_id)
    if not staged:
        raise HTTPException(
            status_code=422,
            detail=f"No staged transactions available for ingest_id {ingest_id}",
        )

    # MVP processes first pending/staged transaction for deterministic posting path.
    pending = staged[0]
    pending_fecha = getattr(pending, "fecha", None)
    pending_total = getattr(pending, "total", None)
    pending_items = getattr(pending, "items", None)
    pending_raw = getattr(pending, "raw_data", None)
    raw_tx = {
        "fecha": pending_fecha.isoformat() if isinstance(pending_fecha, datetime) else None,
        "nit_emisor": _as_str(getattr(pending, "nit_emisor", ""), ""),
        "nit_receptor": _as_str(getattr(pending, "nit_receptor", ""), ""),
        "total": _as_float(pending_total, 0.0),
        "descripcion": _as_str(getattr(pending, "descripcion", ""), ""),
        "items": pending_items if isinstance(pending_items, list) else [],
        "raw_data": pending_raw if isinstance(pending_raw, dict) else {},
    }

    process_job = db_service.create_process_job(db, ingest_id)
    process_id = _as_str(getattr(process_job, "id", ""), "")
    db_service.update_process_job(
        db,
        process_id,
        status=ProcessStatus.RUNNING,
        current_stage="queued",
        current_agent="process_supervisor",
        progress=10,
        agent_log_entry={"agent": "process_supervisor", "stage": "queued", "status": "running"},
    )

    result = invoke_process_pipeline(
        ingest_id=ingest_id,
        raw_transactions=[raw_tx],
        pending_transaction_id=_as_str(getattr(pending, "id", ""), ""),
        process_id=process_id,
    )

    if result.get("error"):
        db_service.update_process_job(
            db,
            process_id,
            status=ProcessStatus.FAILED,
            current_stage="failed",
            current_agent="contador",
            progress=100,
            error_message=_as_str(result.get("error"), "unknown error"),
            agent_log_entry={"agent": "contador", "stage": "failed", "status": "failed"},
        )
        return ProcessResponse(
            message=f"Accounting process failed for ingest_id: {ingest_id}",
            process_id=process_id,
            status=ProcessStatus.FAILED.value,
            ingest_id=ingest_id,
            current_stage="failed",
        )

    db_service.update_process_job(
        db,
        process_id,
        status=ProcessStatus.COMPLETED,
        current_stage="completed",
        current_agent="db_persist",
        progress=100,
        agent_log_entry={"agent": "db_persist", "stage": "completed", "status": "completed"},
    )

    return ProcessResponse(
        message=f"Accounting process completed for ingest_id: {ingest_id}",
        process_id=process_id,
        status=ProcessStatus.COMPLETED.value,
        ingest_id=ingest_id,
        current_stage="completed",
    )


@router.get("/status/{process_id}", response_model=ProcessStatusResponse)
async def get_process_status(process_id: str, db: Session = Depends(get_db)):
    """Get process status for polling/observability."""
    job = db_service.get_process_job(db, process_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Process ID {process_id} not found")

    job_status = getattr(job, "status", None)
    return ProcessStatusResponse(
        process_id=_as_str(getattr(job, "id", ""), ""),
        ingest_id=_as_str(getattr(job, "ingest_id", ""), ""),
        status=getattr(job_status, "value", _as_str(job_status, "queued")),
        current_stage=_as_str(getattr(job, "current_stage", None), "") or None,
        current_agent=_as_str(getattr(job, "current_agent", None), "") or None,
        progress=int(getattr(job, "progress", 0) or 0),
        error_message=_as_str(getattr(job, "error_message", None), "") or None,
        agent_log=getattr(job, "agent_log", []) if isinstance(getattr(job, "agent_log", []), list) else [],
        created_at=getattr(job, "created_at", None),
        started_at=getattr(job, "started_at", None),
        completed_at=getattr(job, "completed_at", None),
    )
