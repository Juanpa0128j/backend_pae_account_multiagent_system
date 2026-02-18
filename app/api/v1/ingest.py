import logging
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.models.schemas import IngestResponse
from app.agents.graph import invoke_agent

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
async def upload_file(file: UploadFile = File(...)):
    """
    Upload and process a PDF file (receipt/invoice).
    
    The file is:
    1. Saved to a temporary location
    2. Processed through the agent graph (Supervisor → Ingesta)
    3. Text extracted with PyPDF
    4. Gemini interprets the structured data
    5. Result returned as JSON
    
    Args:
        file: PDF file to process
        
    Returns:
        IngestResponse with process_id, status, and extracted data
        
    Raises:
        HTTPException 400: If file is not a PDF
        HTTPException 500: If processing fails
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
        
        # Invoke the agent
        logger.info(f"Invoking agent for: {file.filename}")
        result = invoke_agent(temp_file_path)
        
        # Cleanup temp file
        Path(temp_file_path).unlink(missing_ok=True)
        
        # Map result to response
        return IngestResponse(
            message=result.get("message", "Processing completed"),
            ingest_id=result.get("process_id", ""),
            status=result.get("status", "error")
        )
        
    except Exception as e:
        logger.error(f"Error processing file {file.filename}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing file: {str(e)}"
        )
