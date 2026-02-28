"""
Ingesta (Ingest) worker node for the agent graph.
Extracts text from PDFs and uses Gemini to interpret structured data.

On retry (when correction_feedback is present), the agent re-sends the
raw text to Gemini along with the schema errors so the model can self-correct.
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
    1. Extract raw text from PDF using PyPDF  (skipped on retry)
    2. Send text to Gemini for structured interpretation
       — includes correction_feedback on retries
    3. Store the parsed dict in interpreted_data
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
    is_retry = bool(state.get("correction_feedback"))
    
    try:
        # Step 1: Extract raw text (skip if we already have it from a prior attempt)
        if not is_retry or not state.get("raw_text"):
            logger.info(f"Ingest: Extracting text from {file_path}")
            raw_text = extract_text_from_pdf(file_path)
            state["raw_text"] = raw_text
        else:
            raw_text = state["raw_text"]
            logger.info(
                f"Ingest (retry {state.get('retry_count', 1)}): "
                "Re-using previously extracted text"
            )
        
        if not raw_text.strip():
            state["error"] = "No readable text found in PDF"
            logger.warning(state["error"])
            return state
        
        # Step 2: Send to Gemini for interpretation
        gemini_client = GeminiClient()

        if is_retry:
            logger.info(
                f"Ingest: Re-sending to Gemini with correction feedback "
                f"(attempt {state.get('retry_count', 1)})"
            )
            interpreted_data = gemini_client.extract_receipt_data(
                raw_text,
                correction_feedback=state["correction_feedback"],
            )
            # Clear correction feedback after using it
            state["correction_feedback"] = None
        else:
            logger.info("Ingest: Sending to Gemini for interpretation")
            interpreted_data = gemini_client.extract_receipt_data(raw_text)

        state["interpreted_data"] = interpreted_data
        
        # Step 3: Format result (will be enriched by validate_output_node)
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
