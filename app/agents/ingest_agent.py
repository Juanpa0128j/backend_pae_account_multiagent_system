"""
Ingesta (Ingest) worker node for the agent graph.

Supports multiple document formats (PDF, XLSX) and routes interpretation
to the appropriate Gemini extraction method based on document classification.

On retry (when correction_feedback is present), the agent re-sends the
raw text to Gemini along with the schema errors so the model can self-correct.
"""

import uuid
from pathlib import Path

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.config import get_settings
from app.core.gemini_client import get_gemini_client
from app.core.logger import get_logger

try:
    from llama_parse import LlamaParse  # type: ignore[import-untyped]
except ImportError:
    LlamaParse = None  # type: ignore[assignment,misc]

logger = get_logger("app.agents.ingest")

MAX_NODE_RETRIES = 3
_TRANSIENT_EXC = (TimeoutError, ConnectionError, OSError)


# Dispatch table: doc_type → GeminiClient method name
_EXTRACT_METHOD_MAP: dict[str, str] = {
    "factura_venta": "extract_transactions",
    "factura_compra": "extract_transactions",
    "nota_credito": "extract_transactions",
    "nota_debito": "extract_transactions",
    "extracto_bancario": "extract_bank_statement",
    "declaracion_iva": "extract_tax_declaration",
    "declaracion_reteica": "extract_tax_declaration",
    "anexo_tributario": "extract_tax_annex",
    "auxiliar_impuesto": "extract_auxiliary_ledger",
    "balance_general": "extract_financial_statement",
    "estado_resultados": "extract_financial_statement",
    "libro_auxiliar": "extract_auxiliary_ledger",
}


def _gemini_with_retry(client, raw_text: str, correction_feedback=None):
    """
    Call client.extract_transactions() with up to MAX_NODE_RETRIES attempts
    on transient network errors. Non-transient exceptions propagate immediately.
    """
    return _gemini_with_retry_generic(
        client.extract_transactions, raw_text, correction_feedback=correction_feedback
    )


def _gemini_with_retry_generic(method, raw_text: str, correction_feedback=None):
    """
    Call any Gemini extraction method with transient error retry.
    """
    last_exc = None
    for attempt in range(1, MAX_NODE_RETRIES + 1):
        try:
            return method(raw_text, correction_feedback=correction_feedback)
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
    Ingest node: Extracts from document (PDF/XLSX) and interprets with Gemini.

    Supports multiple formats and routes interpretation to the appropriate
    Gemini method based on document classification from the supervisor.
    """
    # If supervisor already flagged an error, skip processing
    if state.get("error"):
        logger.warning(f"Skipping ingest due to upstream error: {state['error']}")
        return state

    file_path = state["file_path"]
    ext = Path(file_path).suffix.lower()
    is_retry = bool(state.get("correction_feedback"))
    settings = get_settings()

    append_log(state, "ingesta", "node_start", {
        "file_path": file_path,
        "format": ext,
        "is_retry": is_retry,
    })

    try:
        # Step 1: Extract raw text (format-aware)
        if not is_retry or not state.get("raw_text"):
            if ext == ".xlsx":
                # Excel: may already be extracted by supervisor classify step
                if not state.get("raw_text"):
                    from app.services.excel_parser import parse_excel
                    logger.info(f"Ingest: Extracting text from {file_path} using excel_parser")
                    raw_text, tabular_data = parse_excel(file_path)
                    state["raw_text"] = raw_text
                    state["parsed_content"] = tabular_data
                else:
                    logger.info("Ingest: Re-using Excel text extracted by supervisor")
                raw_text = state["raw_text"]
            elif ext == ".pdf":
                logger.info(f"Ingest: Extracting text from {file_path} using LlamaParse")
                if LlamaParse is not None:
                    parser = LlamaParse(
                        api_key=settings.llama_cloud_api_key,
                        result_type="markdown",
                    )
                else:
                    raise RuntimeError(
                        "LlamaParse client is not available. "
                        "Install llama-parse and configure LLAMA_CLOUD_API_KEY."
                    )
                documents = parser.load_data(file_path)
                raw_text = "\n\n".join([doc.text for doc in documents])
                state["raw_text"] = raw_text
            else:
                state["error"] = f"Unsupported file format: {ext}"
                logger.error(state["error"])
                append_log(state, "ingesta", "node_error", {"error": state["error"]})
                return state
        else:
            raw_text = state["raw_text"]
            logger.info(
                f"Ingest (retry {state.get('retry_count', 1)}): "
                "Re-using previously extracted text"
            )

        stripped_text = raw_text.strip()
        if not stripped_text:
            state["error"] = "No readable text found in document"
            logger.warning(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        if len(stripped_text) < 50:
            state["error"] = (
                f"Extracted text too short ({len(stripped_text)} chars) "
                "— document may be corrupted or empty"
            )
            logger.warning(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        append_log(state, "ingesta", "extraction_complete", {
            "text_chars": len(raw_text),
        })

        # Step 2: Send to Gemini for interpretation (doc-type-aware)
        gemini_client = get_gemini_client()
        correction_feedback = state.get("correction_feedback") if is_retry else None
        classification = state.get("document_classification") or {}
        doc_type = classification.get("doc_type", "otro")

        if is_retry:
            logger.info(
                f"Ingest: Re-sending to Gemini with correction feedback "
                f"(attempt {state.get('retry_count', 1)})"
            )
        else:
            logger.info(
                "Ingest: Sending to Gemini for interpretation (doc_type=%s)", doc_type
            )

        # Dispatch to the appropriate extraction method
        method_name = _EXTRACT_METHOD_MAP.get(doc_type, "extract_transactions")
        extract_method = getattr(gemini_client, method_name)
        interpreted_data = _gemini_with_retry_generic(
            extract_method, raw_text, correction_feedback=correction_feedback
        )
        # Clear correction feedback after using it
        state["correction_feedback"] = None

        state["interpreted_data"] = interpreted_data

        # Validate Gemini response structure
        if not isinstance(interpreted_data, dict):
            state["error"] = (
                f"Gemini returned invalid structure: expected dict, "
                f"got {type(interpreted_data).__name__}"
            )
            logger.error(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        raw_txs = interpreted_data.get("transactions", [])
        if not isinstance(raw_txs, list):
            state["error"] = "Gemini 'transactions' field is not a list"
            logger.error(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        if not raw_txs:
            state["error"] = "Gemini extracted zero transactions from document"
            logger.warning(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

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
