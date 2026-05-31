from app.agents.state import AgentState
from app.agents.validation_rules import MAX_AUDITOR_RETRIES, MAX_CONTADOR_RETRIES

__all__ = [
    "MAX_CONTADOR_RETRIES",
    "MAX_AUDITOR_RETRIES",
    "should_retry_agent",
    "should_retry_contador",
    "should_retry_auditor",
    "route_after_supervisor",
]


def should_retry_agent(state: AgentState) -> str:
    """Conditional edge for ingest graph: retry, error bypass, or proceed."""
    if state.get("error"):
        return "error"
    if state.get("correction_feedback"):
        return "retry"
    return "end"


def should_retry_contador(state: AgentState) -> str:
    """Conditional edge for contador retries in the process graph."""
    if (
        state.get("correction_feedback")
        and state.get("retry_count", 0) < MAX_CONTADOR_RETRIES
    ):
        return "retry"
    return "end"


def should_retry_auditor(state: AgentState) -> str:
    """Conditional edge for auditor retries in the process graph."""
    if (
        state.get("correction_feedback")
        and state.get("retry_count", 0) < MAX_AUDITOR_RETRIES
    ):
        return "retry"
    return "end"


def route_after_supervisor(state: AgentState) -> str:
    """
    Conditional edge: dispatch to the correct agent node after supervisor routing.
    Returns the node name that matches the routing_map in create_agent_graph().
    """
    agent = state.get("current_agent", "ingesta")
    # audit_review_terminal takes priority over error — it's the HITL path for
    # recoverable audit give-ups (state["error"] is kept for backward compat only).
    if agent == "audit_review_terminal":
        return "audit_review_terminal"
    if state.get("error"):
        return "error_terminal"
    routing_map = {
        "ingesta": "ingesta",
        "ingest": "ingesta",
        "import_existing": "import_existing",
        "contador": "contador",
        "tributario": "tributario",
        "auditor": "auditor",
        "db_persist": "db_persist",
        "persist": "db_persist",
        "reportero": "reportero",
        "review_terminal": "review_terminal",
        "audit_review_terminal": "audit_review_terminal",
    }
    return routing_map.get(agent, "error_terminal")
