"""
Ingesta (Ingest) worker node for the agent graph.
Extracts text using LlamaParse and uses Gemini to interpret structured data as a list of RawTransaction.

On retry (when correction_feedback is present), the agent re-sends the
raw text to Gemini along with the schema errors so the model can self-correct.
"""

import uuid

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.config import get_settings
from app.core.gemini_client import get_gemini_client
from app.core.logger import get_logger
from llama_cloud import LlamaCloud

logger = get_logger("app.agents.ingest")

MAX_NODE_RETRIES = 3
_TRANSIENT_EXC = (TimeoutError, ConnectionError, OSError)


def _gemini_with_retry(client, raw_text: str, correction_feedback=None):
    """
    Call client.extract_transactions() with up to MAX_NODE_RETRIES attempts
    on transient network errors. Non-transient exceptions propagate immediately.
    """
    last_exc = None
    for attempt in range(1, MAX_NODE_RETRIES + 1):
        try:
            return client.extract_transactions(
                raw_text, correction_feedback=correction_feedback
            )
        except _TRANSIENT_EXC as e:
            last_exc = e
            logger.warning(
                f"Ingest: Gemini transient error attempt {attempt}/{MAX_NODE_RETRIES}: {e}"
            )
        except Exception:
            raise
    raise last_exc


def ingest_node(state: AgentState) -> AgentState:
    """
    Ingest node: Extracts multimodally from document and interprets with Gemini.
    """
    # If supervisor already flagged an error, skip processing
    if state.get("error"):
        logger.warning(f"Skipping ingest due to upstream error: {state['error']}")
        return state

    file_path = state["file_path"]
    is_retry = bool(state.get("correction_feedback"))
    settings = get_settings()

    append_log(state, "ingesta", "node_start", {
        "file_path": file_path,
        "is_retry": is_retry,
    })

    try:
        # Step 1: Extract raw text
        if not is_retry or not state.get("raw_text"):
            logger.info(f"Ingest: Extracting text from {file_path} using LlamaCloud")
            parser = LlamaCloud(
                api_key=settings.llama_cloud_api_key,
                result_type="markdown",
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
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        append_log(state, "ingesta", "extraction_complete", {
            "text_chars": len(raw_text),
        })

        # Step 2: Send to Gemini for interpretation (with transient retry)
        gemini_client = get_gemini_client()
        correction_feedback = state.get("correction_feedback") if is_retry else None

        if is_retry:
            logger.info(
                f"Ingest: Re-sending to Gemini with correction feedback "
                f"(attempt {state.get('retry_count', 1)})"
            )
        else:
            logger.info("Ingest: Sending to Gemini for interpretation")

        interpreted_data = _gemini_with_retry(
            gemini_client, raw_text, correction_feedback=correction_feedback
        )
        # Clear correction feedback after using it
        state["correction_feedback"] = None

        state["interpreted_data"] = interpreted_data

        # Extract transactions list
        raw_txs = interpreted_data.get("transactions", [])
        state["raw_transactions"] = raw_txs

        append_log(state, "ingesta", "interpretation_complete", {
            "tx_count": len(raw_txs),
        })

        # Step 3: Format result
        state["result"] = {
            "process_id": str(uuid.uuid4()),
            "status": "completed",
            "data": raw_txs,
            "message": "Document successfully processed",
        }

        logger.info(f"Ingest: Processing complete for {file_path}")

    except Exception as e:
        state["error"] = f"Ingest error: {str(e)}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "ingesta", "node_error", {"error": str(e)})
        state["result"] = {
            "process_id": str(uuid.uuid4()),
            "status": "error",
            "error": state["error"],
            "message": "Failed to process document",
        }

    return state
