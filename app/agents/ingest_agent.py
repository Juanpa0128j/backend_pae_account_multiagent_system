"""
Ingesta (Ingest) worker node for the agent graph.
Extracts text from PDFs and uses Gemini to interpret structured data.
"""

import logging
import uuid
from app.agents.state import AgentState
from app.services.pdf_processor import extract_text_from_pdf
from app.core.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


def ingest_node(state: AgentState) -> AgentState:
    """
    Ingest node: Extracts text from PDF and interprets with Gemini.
    
    Process:
    1. Extract raw text from PDF using PyPDF
    2. Send text to Gemini for structured interpretation
    3. Validate and format the result
    4. Mark as completed or error
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state with extracted data
    """
    
    # If supervisor already flagged an error, skip processing
    if state.get("error"):
        logger.warning(f"Skipping ingest due to upstream error: {state['error']}")
        return state
    
    file_path = state["file_path"]
    
    try:
        # Step 1: Extract raw text from PDF
        logger.info(f"Ingest: Extracting text from {file_path}")
        raw_text = extract_text_from_pdf(file_path)
        state["raw_text"] = raw_text
        
        if not raw_text.strip():
            state["error"] = "No readable text found in PDF"
            logger.warning(state["error"])
            return state
        
        # Step 2: Send to Gemini for interpretation
        logger.info(f"Ingest: Sending to Gemini for interpretation")
        gemini_client = GeminiClient()
        interpreted_data = gemini_client.extract_receipt_data(raw_text)
        state["interpreted_data"] = interpreted_data
        
        # Step 3: Format final result
        state["result"] = {
            "process_id": str(uuid.uuid4()),
            "status": "completed",
            "data": interpreted_data,
            "message": "Receipt/invoice successfully processed"
        }
        
        logger.info(f"Ingest: Processing complete for {file_path}")
        
    except Exception as e:
        state["error"] = f"Ingest error: {str(e)}"
        logger.error(state["error"], exc_info=True)
        state["result"] = {
            "process_id": str(uuid.uuid4()),
            "status": "error",
            "error": state["error"],
            "message": "Failed to process document"
        }
    
    return state
