import logging
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks, status
from sqlalchemy.orm import Session
from app.models.schemas import IngestResponse, IngestDetailResponse
from app.agents.graph import invoke_ingest_pipeline
from app.core.database import get_db
from app.services import db_service
from app.models.database import IngestStatus

logger = logging.getLogger(__name__)
router = APIRouter()

def save_temp_file(file_content: bytes, filename: str) -> str:
    """Save file to temporary directory."""
    temp_dir = Path(tempfile.gettempdir()) / "pae_uploads"
    temp_dir.mkdir(exist_ok=True)
    temp_path = temp_dir / filename
    
    with open(temp_path, "wb") as f:
        f.write(file_content)
    
    return str(temp_path)

def process_ingest_background(temp_file_path: str, ingest_id: str):
    logger.info(f"Invoking background agent for: {ingest_id}")
    try:
        invoke_ingest_pipeline(
            temp_file_path,
            initial_state={"ingest_id": ingest_id},
        )
    except Exception as e:
        logger.error(f"Error in background ingest {ingest_id}: {e}", exc_info=True)
    finally:
        Path(temp_file_path).unlink(missing_ok=True)

@router.post("/upload", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload and process a PDF/Excel/XML/image file (receipt/invoice/scan).
    Returns 202 Accepted immediately.
    """
    # Validate file type
    if not file.filename.lower().endswith(('.pdf', '.xlsx', '.xml', '.jpg', '.jpeg', '.png')):
        raise HTTPException(
            status_code=422,
            detail="Unsupported file type. Accepted: PDF, Excel, XML, JPG, PNG",
            headers={"error_code": "INVALID_FILE_TYPE"}
        )
    
    try:
        file_content = await file.read()
        temp_file_path = save_temp_file(file_content, file.filename)
        logger.info(f"Saved uploaded file to: {temp_file_path}")
        
        ingest_job = db_service.create_ingest_job(db, file.filename, temp_file_path)
        logger.info(f"Created IngestJob: {ingest_job.id}")
        
        background_tasks.add_task(process_ingest_background, temp_file_path, str(ingest_job.id))
        
        return IngestResponse(
            message="File uploaded successfully and queued for processing",
            ingest_id=str(ingest_job.id),
            status=ingest_job.status.value,
            file_name=file.filename,
            created_at=ingest_job.created_at,
            extracted_transactions=0,
            raw_preview=None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing file {file.filename}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Error queueing file: {str(e)}"
        )

@router.get("/{ingest_id}", response_model=IngestDetailResponse)
async def get_ingest_status(ingest_id: str, db: Session = Depends(get_db)):
    """Get the status of an ingest job."""
    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingest ID {ingest_id} not found")
    
    raw_txs = []
    # If using SQLAlchemy relationship properly
    for tx in job.transactions_pending:
        raw_txs.append({
            "fecha": tx.fecha.isoformat() if tx.fecha else "",
            "nit_emisor": tx.nit_emisor or "",
            "nit_receptor": tx.nit_receptor or "",
            "total": float(tx.total) if tx.total is not None else 0.0,
            "descripcion": tx.descripcion,
            "items": tx.items if isinstance(tx.items, list) else []
        })

    # Reconcile stale ingest states: if transactions are already staged but the
    # job is still pending/processing, mark it as completed so clients can
    # advance to the accounting phase.
    # Guard: only reconcile if the job was created more than 60 seconds ago
    # to avoid racing with the background ingest task.
    from datetime import datetime, timezone, timedelta
    is_stale = (
        job.created_at
        and (datetime.now(timezone.utc) - job.created_at) > timedelta(seconds=60)
    )
    if (
        raw_txs
        and is_stale
        and job.status in (IngestStatus.PENDING_PROCESSING, IngestStatus.PROCESSING)
        and not job.extraction_errors
    ):
        updated = db_service.update_ingest_job(db, ingest_id, IngestStatus.COMPLETED)
        if updated:
            job = updated
    
    return {
        "ingest_id": job.id,
        "file_name": job.file_name,
        "status": job.status.value if job.status else "unknown",
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "extraction_errors": job.extraction_errors or [],
        "raw_transactions": raw_txs
    }
