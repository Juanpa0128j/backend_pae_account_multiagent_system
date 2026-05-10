"""Thin supervisor dispatcher.

Initializes defaults and delegates to per-pipeline routers based on mode.
All heavy logic lives in ingest_router, process_router, and reporting_router.
"""

from app.agents.agent_utils import append_log
from app.agents.routing import edge_functions, ingest_router, process_router
from app.agents.state import AgentState
from app.core.logger import get_logger

logger = get_logger("app.agents.routing.supervisor_node")


def supervisor_node(state: AgentState) -> AgentState:
    """Thin dispatcher: init defaults → route by mode."""
    for field, default in [
        ("validation_history", []),
        ("current_agent", ""),
        ("retry_count", 0),
        ("correction_feedback", None),
        ("agent_log", []),
        ("audit_decision", None),
        ("audit_feedback", None),
        ("audit_rejection_count", 0),
    ]:
        if state.get(field) is None:
            state[field] = default

    mode = state.get("mode", "ingest")
    current = state.get("current_agent", "")

    append_log(
        state,
        "supervisor",
        "routing_start",
        {"mode": mode, "current_agent": current},
    )

    if mode in ("ingest", "") and not current:
        return ingest_router.route(state)
    if mode == "process":
        return process_router.route(state)
    if mode == "reporting":
        state["current_agent"] = "reportero"
        append_log(
            state,
            "supervisor",
            "routing_complete",
            {"next_agent": "reportero", "mode": "reporting"},
        )
        return state

    state["error"] = f"Supervisor: unknown mode '{mode}' / current_agent '{current}'"
    logger.error(state["error"])
    append_log(
        state,
        "supervisor",
        "routing_error",
        {"reason": "unknown_state", "mode": mode, "current_agent": current},
    )
    return state


def route_after_supervisor(state: AgentState) -> str:
    """Conditional edge: dispatch to the correct node after supervisor."""
    agent = state.get("current_agent", "ingesta")
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


def process_supervisor_node(state: AgentState) -> AgentState:
    """Process supervisor: validates staged input and routes to contador worker.

    Kept for backward compatibility with tests that import directly.
    """
    if not state.get("validation_history"):
        state["validation_history"] = []
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("correction_feedback") is None:
        state["correction_feedback"] = None
    if state.get("agent_log") is None:
        state["agent_log"] = []

    raw_txs = state.get("raw_transactions", [])
    if not raw_txs:
        state["error"] = "Process supervisor: no staged transactions to process"
        append_log(state, "supervisor", "routing_error", {"reason": "no_transactions"})
        return state

    state["mode"] = "process"
    state["current_agent"] = "contador"
    state["current_stage"] = "routing"
    append_log(state, "supervisor", "routing_complete", {"next_agent": "contador"})
    return state


# Backward-compat re-exports
should_retry_agent = edge_functions.should_retry_agent
should_retry_contador = edge_functions.should_retry_contador
should_retry_auditor = edge_functions.should_retry_auditor
