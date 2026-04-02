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
from app.core.gemini_client import get_gemini_client

logger = logging.getLogger(__name__)


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
        gemini = get_gemini_client()

        if is_retry:
            logger.info(
                "contador: retry attempt %d with correction feedback",
                state.get("retry_count", 1),
            )

        contador_output = gemini.extract_contador_output(
            raw_transactions=raw_transactions,
            rag_context=rag_context,
            correction_feedback=state.get("correction_feedback") if is_retry else None,
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
