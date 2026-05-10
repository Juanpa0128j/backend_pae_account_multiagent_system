"""Terminal nodes for the unified agent graph."""

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.logger import get_logger

logger = get_logger("app.agents.routing.terminal_nodes")


def error_terminal_node(state: AgentState) -> AgentState:
    """Terminal node for unrecoverable errors."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "error"
    state["result"]["error"] = state.get("error", "Unknown error")
    append_log(
        state,
        "supervisor",
        "pipeline_aborted",
        {"reason": state.get("error")},
    )
    logger.error("Pipeline aborted: %s", state.get("error"))
    return state


def review_terminal_node(state: AgentState) -> AgentState:
    """Terminal node for pending_review state."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "pending_review"
    append_log(
        state,
        "supervisor",
        "pipeline_paused",
        {"reason": "pending_review"},
    )
    return state


def audit_review_terminal_node(state: AgentState) -> AgentState:
    """Terminal node: audit gave up — awaits user confirmation."""
    if not state.get("result"):
        state["result"] = {}
    state["result"]["status"] = "pending_audit_review"
    state["result"]["giveup_record"] = state.get("giveup_record")
    state["result"]["audit_rejection_reason"] = state.get(
        "audit_rejection_reason"
    ) or state.get("audit_feedback")
    append_log(
        state,
        "supervisor",
        "pipeline_paused",
        {"reason": "pending_audit_review"},
    )
    return state
