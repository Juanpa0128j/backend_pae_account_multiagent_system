"""
Ingesta (Ingest) worker node for the agent graph.

Supports multiple document formats (PDF, XLSX, and images JPG/PNG) and routes
interpretation to the appropriate Gemini extraction method based on document
classification. Images are parsed via LlamaParse identical to PDFs.

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
    # Invoice-like documents
    "factura_venta": "extract_factura_venta",
    "factura_compra": "extract_factura_compra",
    "nota_credito": "extract_nota_credito",
    "nota_debito": "extract_nota_debito",
    # Bank documents
    "extracto_bancario": "extract_bank_statement",
    # Tax declarations
    "declaracion_iva": "extract_tax_declaration",
    "declaracion_reteica": "extract_tax_declaration",
    "declaracion_ica": "extract_declaracion_ica",
    "autorretencion_ica": "extract_autorretencion_ica",
    # Tax annexes and auxiliaries
    "anexo_tributario": "extract_anexo_iva",
    "anexo_iva": "extract_anexo_iva",
    "auxiliar_impuesto": "extract_auxiliary_ledger",
    "auxiliar_iva": "extract_auxiliar_iva",
    # Financial statements (Vía B)
    "balance_general": "extract_balance_general",
    "estado_resultados": "extract_estado_resultados",
    "libro_auxiliar": "extract_auxiliary_ledger",
    "libro_diario": "extract_libro_diario",
    "flujo_de_caja": "extract_flujo_caja",
    "cambios_patrimonio": "extract_cambios_patrimonio",
    "notas_estados_financieros": "extract_notas_financieras",
    # Vouchers (JPG source docs)
    "comprobante_egreso": "extract_comprobante_egreso",
    "documento_soporte": "extract_documento_soporte",
    "recibo_caja": "extract_recibo_caja",
    "nomina": "extract_nomina",
    "conciliacion_bancaria": "extract_conciliacion_bancaria",
    "cuenta_cobro": "extract_cuenta_cobro",
    "planilla_seguridad_social": "extract_planilla_seg_social",
    "recibo_pago_impuesto": "extract_recibo_pago_impuesto",
}

_VIA_B_STATEMENT_TYPES: set[str] = {
    "balance_general",
    "estado_resultados",
    "libro_auxiliar",
    "libro_diario",
    "flujo_de_caja",
    "cambios_patrimonio",
    "notas_estados_financieros",
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
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini extraction failed without a captured exception")


def ingest_node(state: AgentState) -> AgentState:
    """
    Ingest node: Extracts from document (PDF/XLSX/XML/image) and interprets with Gemini.

    Supports multiple formats (PDF, XLSX, XML, JPG, JPEG, PNG) and routes interpretation
    to the appropriate Gemini method based on document classification from the supervisor.
    Images are parsed via LlamaParse exactly like PDFs.
    """
    # If supervisor already flagged an error, skip processing
    if state.get("error"):
        logger.warning(f"Skipping ingest due to upstream error: {state['error']}")
        return state

    file_path = state["file_path"]
    ext = Path(file_path).suffix.lower()
    is_retry = bool(state.get("correction_feedback"))
    settings = get_settings()

    append_log(
        state,
        "ingesta",
        "node_start",
        {
            "file_path": file_path,
            "format": ext,
            "is_retry": is_retry,
        },
    )

    try:
        # Step 1: Extract raw text (format-aware)
        if not is_retry or not state.get("raw_text"):
            if ext == ".xlsx":
                # Excel: may already be extracted by supervisor classify step
                if not state.get("raw_text"):
                    from app.services.excel_parser import parse_excel

                    logger.info(
                        f"Ingest: Extracting text from {file_path} using excel_parser"
                    )
                    raw_text, tabular_data = parse_excel(file_path)
                    state["raw_text"] = raw_text
                    state["parsed_content"] = tabular_data
                else:
                    logger.info("Ingest: Re-using Excel text extracted by supervisor")
                raw_text = state["raw_text"]
            elif ext == ".xml":
                logger.info(
                    f"Ingest: Extracting text from {file_path} using XML parser"
                )
                from app.services.xml_parser import parse_xml

                raw_text = parse_xml(file_path)
                state["raw_text"] = raw_text
            elif ext in (".pdf", ".jpg", ".jpeg", ".png"):
                format_label = "image" if ext in (".jpg", ".jpeg", ".png") else "PDF"
                logger.info(
                    f"Ingest: Extracting text from {file_path} ({format_label}) using LlamaParse"
                )
                if LlamaParse is None:
                    raise RuntimeError(
                        "LlamaParse client is not available. "
                        "Install llama-parse and configure LLAMA_CLOUD_API_KEY."
                    )

                # Cache parsed text next to the source file to avoid redundant API calls.
                _cache_dir = Path(file_path).parent / ".parse_cache"
                _safe_name = Path(file_path).name.replace(" ", "_")
                _cache_path = _cache_dir / f"{_safe_name}.md"
                if _cache_path.exists():
                    logger.info(
                        "Ingest: Using cached parse for %s", Path(file_path).name
                    )
                    raw_text = _cache_path.read_text(encoding="utf-8")
                else:
                    try:
                        parser = LlamaParse(
                            api_key=settings.llama_cloud_api_key,
                            result_type="markdown",
                        )
                        documents = parser.load_data(file_path)
                        raw_text = "\n\n".join([doc.text for doc in documents])
                    except (KeyError, Exception) as _parse_err:
                        logger.warning(
                            "LlamaParse markdown mode failed (%s) — retrying with result_type='text'",
                            _parse_err,
                        )
                        raw_text = ""

                    # LlamaParse can silently return empty text on some scanned PDFs
                    # without raising an exception — fall back to plain text mode.
                    if not raw_text.strip():
                        logger.warning(
                            "LlamaParse markdown returned empty text — retrying with result_type='text'"
                        )
                        parser = LlamaParse(
                            api_key=settings.llama_cloud_api_key,
                            result_type="text",
                        )
                        documents = parser.load_data(file_path)
                        raw_text = "\n\n".join([doc.text for doc in documents])

                    # Save to cache (even if empty, to avoid re-hitting the API)
                    _cache_dir.mkdir(parents=True, exist_ok=True)
                    _cache_path.write_text(raw_text, encoding="utf-8")

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
            logger.warning(
                "Ingest: extracted text is very short (%d chars) — proceeding but extraction quality may be low",
                len(stripped_text),
            )
            append_log(
                state,
                "ingesta",
                "short_text_warning",
                {"text_chars": len(stripped_text)},
            )

        append_log(
            state,
            "ingesta",
            "extraction_complete",
            {
                "text_chars": len(raw_text),
            },
        )

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
        if not hasattr(gemini_client, method_name):
            state["error"] = (
                f"Ingest dispatch error: method '{method_name}' is not available "
                f"for doc_type '{doc_type}'"
            )
            logger.error(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        append_log(
            state,
            "ingesta",
            "dispatch_selected",
            {
                "doc_type": doc_type,
                "extract_method": method_name,
                "pathway_hint": (
                    "work_with_existing"
                    if doc_type in _VIA_B_STATEMENT_TYPES
                    else "build_from_scratch"
                ),
            },
        )

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

        # Legacy: old extract_transactions returned {"transactions": [...]}.
        # All document types now return rich structured content objects via
        # dedicated extraction methods — the transactions-list path is no longer used.
        _TRANSACTION_DOC_TYPES: set[str] = set()

        if doc_type in _TRANSACTION_DOC_TYPES:
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
            data_summary = {"tx_count": len(raw_txs)}
            result_data = raw_txs
        else:
            # Non-transaction documents: store interpreted_data directly, raw_transactions empty
            state["raw_transactions"] = []
            data_summary = {
                "doc_type": doc_type,
                "fields": list(interpreted_data.keys()),
            }
            result_data = interpreted_data

        append_log(state, "ingesta", "interpretation_complete", data_summary)

        # Step 3: Format result
        state["result"] = {
            "process_id": str(uuid.uuid4()),
            "status": "completed",
            "data": result_data,
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
