"""
Auditor worker node for the process graph.

Receives the ContadorOutput (journal entries) and the original raw
transactions, then uses Gemini to perform a qualitative audit review
following Colombian NIIF/DIAN standards.

The auditor node produces a structured AuditorOutput that includes:
  - approval decision (aprobado: bool)
  - risk level (nivel_riesgo: bajo/medio/alto/critico)
  - findings list (hallazgos)
  - quality score (puntaje_calidad: 0-100)
  - executive summary (resumen)

Deterministic checks (partida doble balance, PUC existence) are
performed *before* this node by validate_contador_output_node, so
the LLM focuses purely on semantic/qualitative review.

On retry (when correction_feedback is present), the invalid output
and schema errors are re-sent to Gemini for self-correction.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.gemini_client import get_gemini_client

logger = logging.getLogger(__name__)


def auditor_node(state: AgentState) -> AgentState:
    """
    Auditor node: performs semantic audit of the contador journal entries.

    Reads:
        state["contador_output"]     – validated ContadorOutput dict
        state["raw_transactions"]    – original staged transaction dicts
        state["correction_feedback"] – schema errors from previous attempt (retry)

    Writes:
        state["auditor_output"]      – AuditorOutput-compatible dict
        state["audit_approved"]      – bool approval decision
        state["audit_decision"]      – "approved" | "rejected"
        state["audit_feedback"]      – rejection reason (if rejected)
        state["current_stage"]       – "auditor"
        state["current_agent"]       – "auditor"
    """
    if state.get("error"):
        logger.warning("auditor: skipping due to upstream error: %s", state["error"])
        return state

    contador_output = state.get("contador_output") or {}
    if not contador_output:
        state["error"] = "auditor: no contador_output in state – run contador first"
        logger.error(state["error"])
        return state

    raw_transactions = state.get("raw_transactions") or []
    is_retry = bool(state.get("correction_feedback"))
    state["current_agent"] = "auditor"
    state["current_stage"] = "auditor"

    append_log(
        state,
        "auditor",
        "node_start",
        {
            "tx_count": len(raw_transactions),
            "is_retry": is_retry,
        },
    )

    try:
        gemini = get_gemini_client()

        if is_retry:
            logger.info(
                "auditor: retry attempt %d with correction feedback",
                state.get("retry_count", 1),
            )

        auditor_output = gemini.extract_auditor_output(
            contador_output=contador_output,
            raw_transactions=raw_transactions,
            correction_feedback=state.get("correction_feedback") if is_retry else None,
        )

        # Clear correction feedback after consuming it
        state["correction_feedback"] = None

        state["auditor_output"] = auditor_output
        approved = bool(auditor_output.get("aprobado", False))
        state["audit_approved"] = approved
        state["audit_rejection_reason"] = (
            auditor_output.get("resumen") if not approved else None
        )
        # Also set unified field names used by the supervisor FSM
        state["audit_decision"] = "approved" if approved else "rejected"
        state["audit_feedback"] = (
            auditor_output.get("resumen") if not approved else None
        )

        if not state.get("result"):
            state["result"] = {}
        state["result"]["auditor_output"] = auditor_output
        state["result"]["audit_approved"] = approved

        logger.info(
            "auditor: audit complete — aprobado=%s nivel_riesgo=%s puntaje=%s",
            auditor_output.get("aprobado"),
            auditor_output.get("nivel_riesgo"),
            auditor_output.get("puntaje_calidad"),
        )
        append_log(
            state,
            "auditor",
            "node_complete",
            {
                "approved": approved,
                "nivel_riesgo": auditor_output.get("nivel_riesgo"),
            },
        )

    except Exception as exc:
        state["error"] = f"auditor error: {exc}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "auditor", "node_error", {"error": str(exc)})
        if not state.get("result"):
            state["result"] = {}
        state["result"]["status"] = "error"
        state["result"]["error"] = state["error"]

    return state
