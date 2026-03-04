from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database import ProcessStatus
from app.models.schemas import ProcessResponse
from app.services import db_service, jobs

router = APIRouter()


@router.post("/accounting/{ingest_id}", response_model=ProcessResponse)
async def process_accounting(ingest_id: str, db: Session = Depends(get_db)):
    """
    Create a process job and execute the graph asynchronously.

    Returns a process_id for polling:
    - GET /api/v1/process/status/{process_id}
    - GET /api/v1/process/result/{process_id}
    """
    ingest_job = db_service.get_ingest_job(db, ingest_id)
    if not ingest_job:
        raise HTTPException(status_code=404, detail=f"Ingest job {ingest_id} not found")

    process_job = db_service.create_process_job(db, ingest_id)
    jobs.start_process_job(process_job.id)

    return ProcessResponse(
        message=f"Started accounting process for ingest_id: {ingest_id}",
        process_id=process_job.id,
        status=process_job.status.value,
    )


@router.get("/status/{process_id}")
async def get_process_status(process_id: str, db: Session = Depends(get_db)):
    """Polling endpoint for async process job status and progress."""
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        raise HTTPException(status_code=404, detail=f"Process job {process_id} not found")

    return {
        "process_id": process_job.id,
        "status": process_job.status.value,
        "current_stage": process_job.current_stage,
        "current_agent": process_job.current_agent,
        "progress": process_job.progress or 0,
        "error_message": process_job.error_message,
        "agent_log": process_job.agent_log or [],
        "created_at": process_job.created_at.isoformat() if process_job.created_at else None,
        "started_at": process_job.started_at.isoformat() if process_job.started_at else None,
        "completed_at": process_job.completed_at.isoformat() if process_job.completed_at else None,
    }


@router.get("/result/{process_id}")
async def get_process_result(process_id: str, db: Session = Depends(get_db)):
    """Return final processed transactions for a completed process job."""
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        raise HTTPException(status_code=404, detail=f"Process job {process_id} not found")

    if process_job.status != ProcessStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Process job {process_id} is not completed yet "
                f"(current status: {process_job.status.value})"
            ),
        )

    get_result_fn = getattr(db_service, "get_process_result_transactions", None)
    if get_result_fn is None:
        raise HTTPException(
            status_code=500,
            detail="Process result retrieval is not available: "
            "'get_process_result_transactions' is not implemented in db_service.",
        )

    transactions = get_result_fn(db, process_job.ingest_id)

    return {
        "process_id": process_job.id,
        "ingest_id": process_job.ingest_id,
        "status": process_job.status.value,
        "transactions": transactions,
    }
