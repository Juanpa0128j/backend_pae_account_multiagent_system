"""
Contador (Accountant) worker node for the process graph.

Receives staged raw transactions from state, queries the RAG service for
relevant PUC codes/normativa, and uses Gemini to produce a balanced
ContadorOutput (partida doble) following Colombian PUC standards.

On retry (when correction_feedback is present), the previous invalid
output and the schema errors are re-sent to Gemini for self-correction.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)


def _extract_source_taxes_summary(source_doc: dict) -> dict | None:
    """Extract a tax summary dict from a structured ingest extraction document.

    Returns None when no tax-relevant fields are found (so callers can skip
    the optional prompt section entirely).
    """
    result: dict = {}

    # Top-level totals (FacturaVentaContent / FacturaCompraContent use a nested `totales` dict)
    totales = source_doc.get("totales") or {}
    if isinstance(totales, dict):
        for key in (
            "total_iva",
            "total_retenciones",
            "total_inc",
            "total_otros_impuestos",
        ):
            val = totales.get(key)
            if val is not None:
                result[key] = float(val)

    # Flat top-level IVA fields (some schemas put it at root)
    for key in ("total_iva", "total_nota_credito", "total_nota_debito"):
        if key not in result and source_doc.get(key) is not None:
            result[key] = float(source_doc[key])

    # Retenciones aplicadas (list of {tipo, base, tarifa, valor})
    retenciones = source_doc.get("retenciones_aplicadas") or []
    if isinstance(retenciones, list) and retenciones:
        result["retenciones_aplicadas"] = [
            {
                "tipo": str(r.get("tipo", "")),
                "base": float(r.get("base", 0)),
                "tarifa": float(r.get("tarifa", 0)),
                "valor": float(r.get("valor", 0)),
            }
            for r in retenciones
            if isinstance(r, dict)
        ]

    # Item-level tax flags — summarise gravado/excluido/exento counts
    items = source_doc.get("items") or []
    if isinstance(items, list) and items:
        gravado = sum(
            1 for it in items if isinstance(it, dict) and it.get("es_gravado")
        )
        excluido = sum(
            1 for it in items if isinstance(it, dict) and it.get("es_excluido")
        )
        exento = sum(1 for it in items if isinstance(it, dict) and it.get("es_exento"))
        if gravado or excluido or exento:
            result["items_gravado"] = gravado
            result["items_excluido"] = excluido
            result["items_exento"] = exento
            # Base gravable = sum of totals for gravado items only
            base_items = sum(
                float(it.get("valor_total_sin_impuesto") or it.get("valor_total") or 0)
                for it in items
                if isinstance(it, dict) and it.get("es_gravado")
            )
            if base_items > 0:
                result["base_gravable_from_items"] = base_items

    return result if result else None


def contador_node(state: AgentState) -> AgentState:
    """
    Contador node: classifies raw transactions into PUC-coded journal entries.

    Reads:
        state["raw_transactions"]    – list of staged transaction dicts
        state["correction_feedback"] – schema errors from previous attempt (retry)

    Writes:
        state["contador_output"]     – ContadorOutput-compatible dict
        state["current_stage"]       – "contador"
        state["current_agent"]       – "contador"
    """
    if state.get("error"):
        logger.warning("contador: skipping due to upstream error: %s", state["error"])
        return state

    raw_transactions = state.get("raw_transactions") or []
    if not raw_transactions:
        state["error"] = "contador: no raw_transactions in state"
        logger.error(state["error"])
        return state

    is_retry = bool(state.get("correction_feedback"))
    state["current_agent"] = "contador"
    state["current_stage"] = "contador"

    append_log(
        state,
        "contador",
        "node_start",
        {
            "tx_count": len(raw_transactions),
            "is_retry": is_retry,
        },
    )

    # Enrich context with RAG-retrieved PUC context when available
    rag_context: list[dict] = []
    try:
        from app.services.rag_service import get_rag_service

        rag_svc = get_rag_service()
        first_tx = raw_transactions[0] if raw_transactions else {}
        query_text = (
            first_tx.get("descripcion") or first_tx.get("concepto") or "gasto general"
        )
        rag_results = rag_svc.search_normativo(query_text, n_results=5)
        rag_context = rag_results if isinstance(rag_results, list) else []
    except Exception as rag_err:
        logger.warning("contador: RAG lookup failed (non-fatal): %s", rag_err)

    try:
        gemini = get_llm_client()

        if is_retry:
            logger.info(
                "contador: retry attempt %d with correction feedback",
                state.get("retry_count", 1),
            )

        doc_type = (state.get("document_classification") or {}).get("doc_type", "")

        # Extract tax summary from the rich source document (populated by ingest pipeline)
        source_doc = state.get("source_document") or {}
        source_taxes: dict | None = None
        if source_doc:
            source_taxes = _extract_source_taxes_summary(source_doc)

        contador_output = gemini.extract_contador_output(
            raw_transactions=raw_transactions,
            doc_type=doc_type,
            rag_context=rag_context,
            correction_feedback=state.get("correction_feedback") if is_retry else None,
            source_taxes=source_taxes,
        )

        # Clear correction feedback after consuming it
        state["correction_feedback"] = None

        state["contador_output"] = contador_output
        state["interpreted_data"] = contador_output  # keep in sync for validators

        if not state.get("result"):
            state["result"] = {}
        state["result"]["contador_output"] = contador_output
        state["result"]["status"] = "clasificado"

        logger.info("contador: classification complete")
        append_log(state, "contador", "node_complete", {"stage": "classifying"})

    except Exception as exc:
        state["error"] = f"contador error: {exc}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "contador", "node_error", {"error": str(exc)})
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "error"
        state["result"]["error"] = state["error"]

    return state
