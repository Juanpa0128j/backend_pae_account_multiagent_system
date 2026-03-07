"""
Ingesta (Ingest) worker node for the agent graph.
Extracts text using LlamaParse and uses Gemini to interpret structured data as a list of RawTransaction.

On retry (when correction_feedback is present), the agent re-sends the
raw text to Gemini along with the schema errors so the model can self-correct.
"""

import logging
import uuid
from app.agents.state import AgentState
from app.core.gemini_client import get_gemini_client
from llama_parse import LlamaParse
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def ingest_node(state: AgentState) -> AgentState:
    """
    Ingest node: Extracts multimodally from document and interprets with Gemini.
    """
    import nest_asyncio
    # Allow asyncio inside LangGraph node context (must run after event loop is set up)
    nest_asyncio.apply()

    # If supervisor already flagged an error, skip processing
    if state.get("error"):
        logger.warning(f"Skipping ingest due to upstream error: {state['error']}")
        return state
    
    file_path = state["file_path"]
    is_retry = bool(state.get("correction_feedback"))
    settings = get_settings()
    
    try:
        # Step 1: Extract raw text 
        if not is_retry or not state.get("raw_text"):
            logger.info(f"Ingest: Extracting text from {file_path} using LlamaParse")
            
            parser = LlamaParse(
                api_key=settings.llama_cloud_api_key,
                result_type="markdown",
                verbose=True
            )
            documents = parser.load_data(file_path)
            raw_text = "\n\n".join([doc.text for doc in documents])
            state["raw_text"] = raw_text
        else:
            raw_text = state["raw_text"]
            logger.info(
                f"Ingest (retry {state.get('retry_count', 1)}): "
                "Re-using previously extracted text"
            )
        
        if not raw_text.strip():
            state["error"] = "No readable text found in document"
            logger.warning(state["error"])
            return state
        
        # Step 2: Send to Gemini for interpretation
        gemini_client = get_gemini_client()

        if is_retry:
            logger.info(
                f"Ingest: Re-sending to Gemini with correction feedback "
                f"(attempt {state.get('retry_count', 1)})"
            )
            interpreted_data = gemini_client.extract_transactions(
                raw_text,
                correction_feedback=state["correction_feedback"],
            )
            # Clear correction feedback after using it
            state["correction_feedback"] = None
        else:
            logger.info("Ingest: Sending to Gemini for interpretation")
            interpreted_data = gemini_client.extract_transactions(raw_text)

        state["interpreted_data"] = interpreted_data
        
        # We need raw_transactions explicitly from the 'transactions' list
        raw_txs = interpreted_data.get("transactions", [])
        state["raw_transactions"] = raw_txs
        
        # Step 3: Format result
        state["result"] = {
            "process_id": str(uuid.uuid4()),
            "status": "completed",
            "data": raw_txs,
            "message": "Document successfully processed"
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
