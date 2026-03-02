import logging
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.orm import Session
from app.models.schemas import IngestResponse
from app.agents.graph import invoke_agent
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


@router.post("/upload", response_model=IngestResponse)
async def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload and process a PDF file (receipt/invoice).
    
    The file is:
    1. Saved to a temporary location
    2. Tracked in the database as an IngestJob
    3. Processed through the agent graph (Supervisor → Ingesta → Validate → DB Persist)
    4. Text extracted with PyPDF, Gemini interprets, results persisted to PostgreSQL
    5. Result returned as JSON
    """
    
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    
    try:
        # Read file content
        file_content = await file.read()
        
        # Save to temp location
        temp_file_path = save_temp_file(file_content, file.filename)
        logger.info(f"Saved uploaded file to: {temp_file_path}")
        
        # Create IngestJob in DB
        ingest_job = db_service.create_ingest_job(db, file.filename, temp_file_path)
        logger.info(f"Created IngestJob: {ingest_job.id}")
        
        # Invoke the agent (will persist to DB via db_persist node)
        logger.info(f"Invoking agent for: {file.filename}")
        result = invoke_agent(
            temp_file_path,
            initial_state={"ingest_id": str(ingest_job.id)},
        )
        
        # Cleanup temp file
        Path(temp_file_path).unlink(missing_ok=True)
        
        # Map result to response
        return IngestResponse(
            message=result.get("message", "Processing completed"),
            ingest_id=result.get("ingest_id") or result.get("process_id", ingest_job.id),
            status=result.get("status", "error"),
        )
        
    except Exception as e:
        logger.error(f"Error processing file {file.filename}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing file: {str(e)}"
        )


@router.get("/{ingest_id}")
async def get_ingest_status(ingest_id: str, db: Session = Depends(get_db)):
    """Get the status of an ingest job."""
    job = db_service.get_ingest_job(db, ingest_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Ingest job {ingest_id} not found")
    
    return {
        "id": job.id,
        "file_name": job.file_name,
        "status": job.status.value if job.status else "unknown",
        "raw_preview": job.raw_preview,
        "extraction_errors": job.extraction_errors,
        "created_at": str(job.created_at) if job.created_at else None,
        "completed_at": str(job.completed_at) if job.completed_at else None,
    }
