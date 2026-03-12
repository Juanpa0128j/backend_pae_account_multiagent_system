"""
LangGraph StateGraphs for ingest and process pipelines.

Ingest graph:
  Supervisor → Ingesta → ValidateOutput ─┬─(valid)──→ db_persist → END
                  ↑                       └─(retry)──→ Ingesta

Process graph:
  process_supervisor → contador → validate_contador ─┬─(retry)──→ contador
                                                     └─(valid)──→ auditor
  auditor → validate_auditor ─┬─(retry)──→ auditor
                              └─(valid)──→ db_persist → END
"""

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from app.agents.state import AgentState
from app.agents.supervisor import (
    supervisor_node,
    process_supervisor_node,
    validate_output_node,
    validate_contador_output_node,
    validate_auditor_output_node,
    should_retry_agent,
    should_retry_contador,
    should_retry_auditor,
)
from app.agents.ingest_agent import ingest_node
from app.agents.persist_node import db_persist_node
from app.agents.contador_agent import contador_node
from app.agents.auditor_agent import auditor_node

logger = logging.getLogger(__name__)

# Keys that callers are permitted to pre-set via the initial_state parameter.
# Core execution fields are intentionally excluded to prevent accidental
# runtime corruption.
_ALLOWED_INITIAL_STATE_KEYS: frozenset[str] = frozenset({"ingest_id"})


def create_process_graph() -> Any:
    """
    Create and return the process graph.
    """
    graph = StateGraph(AgentState)

    graph.add_node("process_supervisor", process_supervisor_node)
    graph.add_node("contador", contador_node)
    graph.add_node("validate_contador", validate_contador_output_node)
    graph.add_node("auditor", auditor_node)
    graph.add_node("validate_auditor", validate_auditor_output_node)
    graph.add_node("db_persist", db_persist_node)

    graph.add_edge("process_supervisor", "contador")
    graph.add_edge("contador", "validate_contador")
    graph.add_conditional_edges(
        "validate_contador",
        should_retry_contador,
        {
            "retry": "contador",
            "end": "auditor",
        },
    )
    graph.add_edge("auditor", "validate_auditor")
    graph.add_conditional_edges(
        "validate_auditor",
        should_retry_auditor,
        {
            "retry": "auditor",
            "end": "db_persist",
        },
    )
    graph.add_edge("db_persist", END)
    graph.set_entry_point("process_supervisor")

    compiled_graph = graph.compile()
    logger.info(
        "Process graph created and compiled "
        "(contador + validation + auditor + validation + DB persist)"
    )
    return compiled_graph


def create_agent_graph() -> Any:
    """
    Create and return the ingest graph with validation & retry.
    """
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("ingesta", ingest_node)
    graph.add_node("validate_output", validate_output_node)
    graph.add_node("db_persist", db_persist_node)

    graph.add_edge("supervisor", "ingesta")
    graph.add_edge("ingesta", "validate_output")
    graph.add_conditional_edges(
        "validate_output",
        should_retry_agent,
        {
            "retry": "ingesta",
            "end": "db_persist",
        },
    )
    graph.add_edge("db_persist", END)
    graph.set_entry_point("supervisor")

    compiled_graph = graph.compile()
    logger.info("Agent graph created and compiled (with validation loop + DB persist)")
    return compiled_graph


def invoke_agent(file_path: str, initial_state: dict | None = None) -> dict:
    """
    Invoke the ingest graph with a file path.
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
        "auditor_output": {},
        "audit_approved": None,
        "audit_rejection_reason": None,
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

    logger.info("Invoking ingest agent for file: %s", file_path)
    final_state = graph.invoke(state)

    result = final_state["result"]
    result["validation_history"] = final_state.get("validation_history", [])
    result["db_result"] = final_state.get("db_result")
    return result


def invoke_process_pipeline(
    *,
    ingest_id: str,
    raw_transactions: list[dict],
    pending_transaction_id: str,
    process_id: str | None = None,
) -> dict:
    """
    Invoke the accounting process pipeline starting from staged transactions.
    """
    graph = create_process_graph()

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
        "auditor_output": {},
        "audit_approved": None,
        "audit_rejection_reason": None,
    }

    final_state = graph.invoke(state)
    result = final_state["result"]
    result["validation_history"] = final_state.get("validation_history", [])
    result["db_result"] = final_state.get("db_result")
    result["error"] = final_state.get("error")
    return result
