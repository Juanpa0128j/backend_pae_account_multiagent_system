"""
Ingesta (Ingest) worker node for the agent graph.

Supports multiple document formats (PDF, XLSX, and images JPG/PNG) and routes
interpretation to the appropriate LLM extraction method based on document
classification. Images are parsed via LlamaCloud identical to PDFs.

On retry (when correction_feedback is present), the agent re-sends the
raw text to the LLM along with the schema errors so the model can self-correct.

Document-specific enhancements:
- `recibo_caja`: Now extracts tipo_recibo (cobro_cartera | venta_directa | otro)
  and referencia_factura to enable intelligent downstream accounting classification.
"""

import uuid
from pathlib import Path

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.agents.agent_utils import append_log
from app.agents.llm_retry import llm_with_parse_retry
from app.agents.state import AgentState
from app.core.config import get_settings
from app.core.llm_client import get_llm_client
from app.core.logger import get_logger
from app.services.document_mappers import build_structured_transactions
from app.services.parse_cache_service import get_cached_parse, store_parse

try:
    from llama_cloud.client import LlamaCloud  # type: ignore[import-untyped]
except ImportError:
    LlamaCloud = None  # type: ignore[assignment,misc]

logger = get_logger("app.agents.ingest")

_MODE_TO_TIER: dict[str, str] = {
    "fast": "fast",
    "standard": "cost_effective",
    "agentic": "agentic",
    "agentic_plus": "agentic_plus",
}

# Pinned per llama_cloud.resources.parsing.ParsingResource.parse's `version`
# param docs (installed SDK 2.11.0): Literal["2026-01-08", "2025-12-31",
# "2025-12-18", "2025-12-11", "latest"]. "latest" tracks the current stable
# tier configuration per the SDK's own example usage.
PARSE_API_VERSION = "latest"


def _build_parse_options(mode: str) -> dict:
    tier = _MODE_TO_TIER.get(mode)
    if tier is None:
        logger.warning(
            "Unknown parser mode '%s' — falling back to cost_effective tier", mode
        )
        tier = "cost_effective"
    return {
        "tier": tier,
        "version": PARSE_API_VERSION,
        "expand": ["markdown_full", "text_full"],
    }


def _extract_text(result) -> str:
    markdown = getattr(result, "markdown", None) or ""
    if markdown.strip():
        return markdown
    return getattr(result, "text", None) or ""


def _is_transient_parse_error(exc: BaseException) -> bool:
    """True for LlamaCloud parse failures worth retrying — network/timeout/5xx.

    v2 raises typed httpx errors; no more KeyError/RuntimeError string
    sniffing (that was a v1-only failure class from result-not-yet-queryable
    races in the old llama-parse client's low-level polling).
    """
    if isinstance(
        exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


def _parse_with_retry(fn):
    """Run a zero-arg parse callable with retry on transient errors only."""

    def _log_retry(retry_state) -> None:
        logger.warning(
            "LlamaParse transient failure (attempt %d): %r — retrying",
            retry_state.attempt_number,
            retry_state.outcome.exception(),
        )

    for attempt in Retrying(
        retry=retry_if_exception(_is_transient_parse_error),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        before_sleep=_log_retry,
        reraise=True,
    ):
        with attempt:
            return fn()
    raise RuntimeError("unreachable")  # for type-checker


def _parse_single_file(file_path: str, state: AgentState) -> str:
    """Parse a single file and return raw text. Mutates state for Excel parsed_content."""
    ext = Path(file_path).suffix.lower()
    if ext == ".xlsx":
        from app.services.excel_parser import parse_excel

        logger.info("Ingest: Extracting text from %s using excel_parser", file_path)
        raw_text, tabular_data = parse_excel(file_path)
        existing = state.get("parsed_content") or []
        state["parsed_content"] = existing + list(tabular_data)
        return raw_text
    elif ext == ".xml":
        logger.info("Ingest: Extracting text from %s using XML parser", file_path)
        from app.services.xml_parser import parse_xml

        return parse_xml(file_path)
    elif ext in (".pdf", ".jpg", ".jpeg", ".png"):
        format_label = "image" if ext in (".jpg", ".jpeg", ".png") else "PDF"
        logger.info(
            "Ingest: Extracting text from %s (%s) using LlamaParse",
            file_path,
            format_label,
        )
        if LlamaCloud is None:
            raise RuntimeError(
                "LlamaCloud client is not available. "
                "Install llama-cloud and configure LLAMA_CLOUD_API_KEY."
            )

        import hashlib

        settings = get_settings()
        try:
            _file_bytes = Path(file_path).read_bytes()
            _content_hash = hashlib.sha256(_file_bytes).hexdigest()
        except OSError:
            _content_hash = None

        _tier_mode = state.get("parser_mode") or "fast"
        _options = _build_parse_options(_tier_mode)
        if _content_hash:
            _cached = get_cached_parse(_content_hash, _options["tier"])
            if _cached is not None:
                logger.info(
                    "Ingest: Using cached parse for %s (hash=%s..., tier=%s)",
                    Path(file_path).name,
                    _content_hash[:12],
                    _options["tier"],
                )
                return _cached

        client = LlamaCloud(api_key=settings.llama_cloud_api_key)
        with open(file_path, "rb") as fh:
            result = _parse_with_retry(
                lambda: client.parsing.parse(upload_file=fh, **_options)
            )
        raw_text = _extract_text(result)

        if not raw_text.strip():
            raise ValueError(
                f"LlamaParse returned no content for '{Path(file_path).name}' "
                "in either markdown or text form"
            )

        if _content_hash:
            store_parse(_content_hash, _options["tier"], raw_text)

        return raw_text
    else:
        raise ValueError(f"Unsupported file format: {ext}")


# Dispatch table: doc_type → LLMClient method name
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
    "balance_general_anterior": "extract_balance_general",
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
    "liquidacion_cesantias": "extract_liquidacion_cesantias",
    "conciliacion_bancaria": "extract_conciliacion_bancaria",
    "cuenta_cobro": "extract_cuenta_cobro",
    "planilla_seguridad_social": "extract_planilla_seg_social",
    "recibo_pago_impuesto": "extract_recibo_pago_impuesto",
    "nota_ajuste_contable": "extract_documento_soporte",
}

_VIA_B_STATEMENT_TYPES: set[str] = {
    "balance_general",
    "balance_general_anterior",
    "estado_resultados",
    "libro_auxiliar",
    "libro_diario",
    "flujo_de_caja",
    "cambios_patrimonio",
    "notas_estados_financieros",
}


def _merge_document_results(results: list[dict]) -> dict:
    """Merge interpreted_data from multiple independently-processed documents.

    List-valued fields (e.g. 'items', 'transacciones') are concatenated.
    Scalar fields take the last non-None value across results.
    """
    if not results:
        return {}
    if len(results) == 1:
        return results[0]

    list_keys: set[str] = set()
    for r in results:
        for k, v in r.items():
            if isinstance(v, list):
                list_keys.add(k)

    all_keys: set[str] = set().union(*[r.keys() for r in results])
    merged: dict = {}
    for k in all_keys:
        if k in list_keys:
            merged[k] = []
            for r in results:
                if isinstance(r.get(k), list):
                    merged[k].extend(r[k])
        else:
            for r in reversed(results):
                if k in r and r[k] is not None:
                    merged[k] = r[k]
                    break
    return merged


def _ingest_documents_mode(state: AgentState, file_paths: list[str]) -> AgentState:
    """Process each file independently and merge results (multi_file_mode='documents')."""
    llm = get_llm_client()
    classification = state.get("document_classification") or {}
    doc_type = classification.get("doc_type", "otro")
    method_name = _EXTRACT_METHOD_MAP.get(doc_type, "extract_transactions")

    if not hasattr(llm, method_name):
        state["error"] = (
            f"Ingest dispatch error: method '{method_name}' is not available "
            f"for doc_type '{doc_type}'"
        )
        logger.error(state["error"])
        append_log(state, "ingesta", "node_error", {"error": state["error"]})
        return state

    extract_method = getattr(llm, method_name)
    ingest_id = state.get("ingest_id")
    all_results: list[tuple[str, dict]] = []

    for i, fp in enumerate(file_paths):
        if ingest_id:
            from app.core.database import SessionLocal
            from app.services.db_service import update_ingest_file_index

            _db = SessionLocal()
            try:
                update_ingest_file_index(_db, ingest_id, i)
            finally:
                _db.close()

        append_log(state, "ingesta", "parsing_file", {"file_index": i, "file": fp})
        try:
            page_text = _parse_single_file(fp, state)
        except ValueError as _fmt_err:
            state["error"] = str(_fmt_err)
            logger.error(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        result = llm_with_parse_retry(extract_method, page_text, agent_label="ingesta")
        if isinstance(result, dict):
            all_results.append((fp, result))

    # Build per-file tagged transaction dicts for source_file attribution
    tagged_transactions: list[dict] = []
    for fp, result in all_results:
        file_txs = build_structured_transactions(result, doc_type)
        filename = Path(fp).name
        for tx in file_txs:
            tagged_transactions.append({**tx, "source_file": filename})

    merged = _merge_document_results([r for _, r in all_results])
    state["interpreted_data"] = merged
    state["raw_text"] = ""
    state["raw_transactions"] = tagged_transactions
    state["correction_feedback"] = None

    state["result"] = {
        "process_id": str(uuid.uuid4()),
        "status": "completed",
        "data": merged,
        "message": f"Processed {len(file_paths)} independent documents",
    }

    logger.info("Ingest (documents mode): processed %d files", len(file_paths))
    append_log(
        state,
        "ingesta",
        "interpretation_complete",
        {"doc_count": len(file_paths), "doc_type": doc_type},
    )

    from app.agents.audit_utils import append_audit_report
    from app.agents.auditors import ingest_auditor

    _report = ingest_auditor.run(state)
    append_audit_report(state, _report)

    return state


def ingest_node(state: AgentState) -> AgentState:
    """
    Ingest node: Extracts from document (PDF/XLSX/XML/image) and interprets with the LLM.

    Supports multiple formats (PDF, XLSX, XML, JPG, JPEG, PNG) and routes interpretation
    to the appropriate LLM method based on document classification from the supervisor.
    Images are parsed via LlamaCloud exactly like PDFs.
    """
    # If supervisor already flagged an error, skip processing
    if state.get("error"):
        logger.warning(f"Skipping ingest due to upstream error: {state['error']}")
        return state

    file_path = state["file_path"]
    ext = Path(file_path).suffix.lower()
    is_retry = bool(state.get("correction_feedback"))

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
            file_paths = state.get("file_paths") or []
            multi_file_mode = state.get("multi_file_mode") or "pages"
            if len(file_paths) > 1 and multi_file_mode == "documents":
                # Each file is an independent document — call LLM per file, merge results.
                return _ingest_documents_mode(state, file_paths)
            elif len(file_paths) > 1:
                # pages mode: concatenate all files as one document.
                raw_texts = []
                ingest_id = state.get("ingest_id")
                for i, fp in enumerate(file_paths):
                    if ingest_id:
                        from app.core.database import SessionLocal
                        from app.services.db_service import update_ingest_file_index

                        _db = SessionLocal()
                        try:
                            update_ingest_file_index(_db, ingest_id, i)
                        finally:
                            _db.close()
                    try:
                        page_text = _parse_single_file(fp, state)
                    except ValueError as _fmt_err:
                        state["error"] = str(_fmt_err)
                        logger.error(state["error"])
                        append_log(
                            state, "ingesta", "node_error", {"error": state["error"]}
                        )
                        return state
                    raw_texts.append(page_text)
                    if i < len(file_paths) - 1:
                        raw_texts.append(f"--- PAGE {i + 1} ---")
                raw_text = "\n\n".join(raw_texts)
                state["raw_text"] = raw_text
            else:
                file_path = state["file_path"]
                ext = Path(file_path).suffix.lower()
                # Excel-specific optimization: supervisor may have pre-parsed.
                if ext == ".xlsx" and state.get("raw_text"):
                    logger.info("Ingest: Re-using Excel text extracted by supervisor")
                    raw_text = state["raw_text"]
                else:
                    try:
                        raw_text = _parse_single_file(file_path, state)
                    except ValueError as _fmt_err:
                        state["error"] = str(_fmt_err)
                        logger.error(state["error"])
                        append_log(
                            state, "ingesta", "node_error", {"error": state["error"]}
                        )
                        return state
                    state["raw_text"] = raw_text
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
            from app.agents.audit_utils import append_finding
            from app.models.audit import AuditFinding, AuditTarget, Severity

            append_finding(
                state,
                AuditFinding(
                    target=AuditTarget.INGEST,
                    rule_id="ING-EXTRACTION-PARTIAL",
                    severity=Severity.WARNING,
                    fixable=False,
                    responsible_agent="ingest",
                    technical_message=f"Extracted text is very short ({len(stripped_text)} chars) — extraction quality may be low.",
                    user_message_es="La extracción del documento fue parcial. Verifique que el archivo no esté dañado o sea legible.",
                    evidence={"text_chars": len(stripped_text)},
                ),
            )

        append_log(
            state,
            "ingesta",
            "extraction_complete",
            {
                "text_chars": len(raw_text),
            },
        )

        # Step 2: Send to LLM for interpretation (doc-type-aware)
        llm = get_llm_client()
        correction_feedback = state.get("correction_feedback") if is_retry else None
        classification = state.get("document_classification") or {}
        doc_type = classification.get("doc_type", "otro")

        if is_retry:
            logger.info(
                f"Ingest: Re-sending to LLM with correction feedback "
                f"(attempt {state.get('retry_count', 1)})"
            )
        else:
            logger.info(
                "Ingest: Sending to LLM for interpretation (doc_type=%s)", doc_type
            )

        # Dispatch to the appropriate extraction method
        method_name = _EXTRACT_METHOD_MAP.get(doc_type, "extract_transactions")
        if not hasattr(llm, method_name):
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

        extract_method = getattr(llm, method_name)
        interpreted_data = llm_with_parse_retry(
            extract_method,
            raw_text,
            correction_feedback=correction_feedback,
            agent_label="ingesta",
        )
        # Clear correction feedback after using it
        state["correction_feedback"] = None

        state["interpreted_data"] = interpreted_data

        # Validate LLM response structure
        if not isinstance(interpreted_data, dict):
            state["error"] = (
                f"LLM returned invalid structure: expected dict, "
                f"got {type(interpreted_data).__name__}"
            )
            logger.error(state["error"])
            append_log(state, "ingesta", "node_error", {"error": state["error"]})
            return state

        if doc_type == "extracto_bancario":
            from app.services.bank_statement_repair import repair_bank_movements

            interpreted_data, repair_logs = repair_bank_movements(interpreted_data)
            state["interpreted_data"] = interpreted_data
            if repair_logs:
                append_log(
                    state,
                    "ingesta",
                    "bank_statement_repaired",
                    {"repairs": repair_logs, "count": len(repair_logs)},
                )

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

        # Phase 3: deterministic ingest audit
        from app.agents.audit_utils import append_audit_report
        from app.agents.auditors import ingest_auditor

        _ingest_report = ingest_auditor.run(state)
        append_audit_report(state, _ingest_report)

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
