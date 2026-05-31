import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

logger = logging.getLogger(__name__)


def error_terminal_node(state: AgentState) -> AgentState:
    """
    Terminal node for unrecoverable errors detected before pipeline starts.
    Ensures result always has a consistent {status: error} shape.
    """
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "error"
    state["result"]["error"] = state.get("error", "Unknown error")
    state["current_agent"] = "error_terminal"
    append_log(
        state,
        "supervisor",
        "pipeline_aborted",
        {
            "reason": state.get("error"),
        },
    )
    logger.error(f"Pipeline aborted: {state.get('error')}")
    return state


def review_terminal_node(state: AgentState) -> AgentState:
    """Terminal node for pending_review state without error."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "pending_review"
    state["current_agent"] = "review_terminal"
    append_log(
        state,
        "supervisor",
        "pipeline_paused",
        {
            "reason": "pending_review",
        },
    )
    return state


def audit_review_terminal_node(state: AgentState) -> AgentState:
    """Terminal node: audit gave up — awaits user confirmation to force-persist."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "pending_audit_review"
    state["result"]["giveup_record"] = state.get("giveup_record")
    state["result"]["audit_rejection_reason"] = state.get(
        "audit_rejection_reason"
    ) or state.get("audit_feedback")
    state["current_agent"] = "audit_review_terminal"
    append_log(
        state,
        "supervisor",
        "pipeline_paused",
        {"reason": "pending_audit_review"},
    )
    return state
