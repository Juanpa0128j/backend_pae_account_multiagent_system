"""
LangGraph StateGraph for the PAE multi-agent system.

Unified 9-node graph — all pipelines routed via supervisor FSM:
  Pipeline 1 (mode="ingest"):
    supervisor → ingesta → validate_output → [retry|error|end→db_persist] → END
  Pipeline 2 (mode="process"):
    supervisor → contador → supervisor → tributario → supervisor → auditor
      → supervisor → [approved→db_persist | rejected→contador] → END
  Reporting (mode="reporting"):
    supervisor → reportero → END
  Error path:
    supervisor → error_terminal → END
"""

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.auditor_agent import auditor_node
from app.agents.contador_agent import contador_node
from app.agents.ingest_agent import ingest_node
from app.agents.persist_node import db_persist_node
from app.agents.reportero_agent import reportero_node
from app.agents.state import AgentState
from app.agents.supervisor import (
    error_terminal_node,
    route_after_supervisor,
    should_retry_agent,
    supervisor_node,
    validate_output_node,
)
from app.agents.tributario_agent import tributario_node

logger = logging.getLogger(__name__)

# Keys that callers are permitted to pre-set via the initial_state parameter.
_ALLOWED_INITIAL_STATE_KEYS: frozenset[str] = frozenset({"ingest_id", "mode"})


# ---------------------------------------------------------------------------
# Unified 9-node graph
# ---------------------------------------------------------------------------

def create_agent_graph() -> Any:
    """
    Create and compile the unified 9-node agent graph.

    All pipelines are routed by the supervisor FSM via the 'mode' state field:
      "ingest"   → Pipeline 1 (default, backward-compatible)
      "process"  → Pipeline 2 (accounting loop with Contador/Tributario/Auditor)
      "reporting"→ Reporting pipeline

    Returns:
        Compiled StateGraph ready for invocation.
    """
    graph = StateGraph(AgentState)

    # --- register all nodes ---
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("ingesta", ingest_node)
    graph.add_node("validate_output", validate_output_node)
    graph.add_node("db_persist", db_persist_node)
    graph.add_node("error_terminal", error_terminal_node)
    graph.add_node("contador", contador_node)
    graph.add_node("tributario", tributario_node)
    graph.add_node("auditor", auditor_node)
    graph.add_node("reportero", reportero_node)

    # --- supervisor dispatches to the correct worker ---
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "ingesta": "ingesta",
            "contador": "contador",
            "tributario": "tributario",
            "auditor": "auditor",
            "db_persist": "db_persist",
            "reportero": "reportero",
            "error_terminal": "error_terminal",
        },
    )

    # --- Pipeline 1: ingesta → validate → retry or persist ---
    graph.add_edge("ingesta", "validate_output")
    graph.add_conditional_edges(
        "validate_output",
        should_retry_agent,
        {"retry": "ingesta", "end": "db_persist", "error": END},
    )

    # --- Pipeline 2: each accounting agent returns to supervisor ---
    graph.add_edge("contador", "supervisor")
    graph.add_edge("tributario", "supervisor")
    graph.add_edge("auditor", "supervisor")

    # --- terminals ---
    graph.add_edge("reportero", END)
    graph.add_edge("db_persist", END)
    graph.add_edge("error_terminal", END)

    graph.set_entry_point("supervisor")

    compiled = graph.compile()
    logger.info("Unified agent graph compiled — 9 nodes")
    return compiled


# ---------------------------------------------------------------------------
# invoke_agent — Pipeline 1 (ingest) entry point
# ---------------------------------------------------------------------------

def invoke_agent(file_path: str, initial_state: dict | None = None) -> dict:
    """
    Invoke the unified agent graph for a file upload (Pipeline 1).

    Args:
        file_path: Path to the PDF file to process.
        initial_state: Optional dict with supplemental state fields.
            Only keys in _ALLOWED_INITIAL_STATE_KEYS are accepted.

    Returns:
        Result dict with status, data, validation_history, agent_log, db_result.
    """
    graph = create_agent_graph()

    state: AgentState = {
        "file_path": file_path,
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
        "mode": "ingest",
        "raw_transactions": [],
        "contador_output": {},
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": None,
        "agent_log": [],
        "audit_decision": None,
        "audit_feedback": None,
    }

    if initial_state:
        disallowed = set(initial_state.keys()) - _ALLOWED_INITIAL_STATE_KEYS
        if disallowed:
            raise ValueError(
                f"invoke_agent: initial_state contains disallowed keys: {sorted(disallowed)}. "
                f"Permitted keys: {sorted(_ALLOWED_INITIAL_STATE_KEYS)}"
            )
        for key, value in initial_state.items():
            state[key] = value

    logger.info(f"Invoking agent for file: {file_path}")
    final_state = graph.invoke(state)

    result = final_state["result"]
    result["validation_history"] = final_state.get("validation_history", [])
    result["db_result"] = final_state.get("db_result")
    result["agent_log"] = final_state.get("agent_log", [])

    return result


# ---------------------------------------------------------------------------
# invoke_process_pipeline — Pipeline 2 (accounting) entry point
# ---------------------------------------------------------------------------

def invoke_process_pipeline(
    *,
    ingest_id: str,
    raw_transactions: list[dict],
    pending_transaction_id: str,
    process_id: str | None = None,
) -> dict:
    """
    Invoke the unified agent graph for accounting (Pipeline 2, mode='process').
    """
    graph = create_agent_graph()

    state: AgentState = {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": ingest_id,
        "db_result": None,
        "mode": "process",
        "raw_transactions": raw_transactions,
        "contador_output": {},
        "process_id": process_id,
        "pending_transaction_id": pending_transaction_id,
        "current_stage": "queued",
        "agent_log": [],
        "audit_decision": None,
        "audit_feedback": None,
    }

    final_state = graph.invoke(state)
    result = final_state["result"]
    result["validation_history"] = final_state.get("validation_history", [])
    result["db_result"] = final_state.get("db_result")
    result["error"] = final_state.get("error")
    result["agent_log"] = final_state.get("agent_log", [])
    return result
