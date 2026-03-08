from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database import ProcessStatus
from app.models.schemas import ProcessResponse, ProcessStatusResponse, ProcessResultResponse
from app.services import db_service, jobs

router = APIRouter()


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
async def get_process_status(process_id: str, db: Session = Depends(get_db)):
    """Polling endpoint for async process job status and progress."""
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        raise HTTPException(status_code=404, detail=f"Process job {process_id} not found")

    return ProcessStatusResponse(
        process_id=process_job.id,
        status=process_job.status.value,
        current_stage=process_job.current_stage,
        current_agent=process_job.current_agent,
        progress=process_job.progress,
        error_message=process_job.error_message,
        agent_log=process_job.agent_log or [],
        created_at=process_job.created_at.isoformat() if process_job.created_at else None,
        started_at=process_job.started_at.isoformat() if process_job.started_at else None,
        completed_at=process_job.completed_at.isoformat() if process_job.completed_at else None,
    )


@router.get("/result/{process_id}", response_model=ProcessResultResponse)
async def get_process_result(process_id: str, db: Session = Depends(get_db)):
    """Return final processed transactions for a completed process job."""
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        raise HTTPException(status_code=404, detail=f"Process job {process_id} not found")

    if process_job.status != ProcessStatus.COMPLETED:
        raise HTTPException(
            status_code=202,
            detail=(
                f"Process job {process_id} is still being processed "
                f"(current status: {process_job.status.value}). "
                f"Poll /api/v1/process/status/{process_id} for updates."
            ),
        )

    transactions = db_service.get_process_result_transactions(db, process_job.ingest_id)

    return ProcessResultResponse(
        process_id=process_job.id,
        ingest_id=process_job.ingest_id,
        status=process_job.status.value,
        transactions=transactions,
    )
