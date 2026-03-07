"""
Agente Reportero (Analyst) — STUB for Sprint 9.
Full implementation: Sprint 15.

Role (docs/Diseño de arquitectura de agente):
  - Triggered by GET /reports/* and /tax/* API endpoints.
  - Queries SQL Libro Mayor and generates PDF/Excel reports.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

logger = logging.getLogger(__name__)


def reportero_node(state: AgentState) -> AgentState:
    """
    Reportero stub node — returns empty report structure until Sprint 15.
    """
    if state.get("error"):
        return state
    append_log(state, "reportero", "node_start", {"stub": True, "sprint": 15})
    logger.info("Reportero: STUB — no-op until Sprint 15")
    if not state.get("result"):
        state["result"] = {}
    state["result"]["report"] = {
        "status": "stub",
        "message": "Report generation not yet implemented (Sprint 15)",
    }
    state["current_stage"] = "reporting_complete"
    append_log(state, "reportero", "node_complete", {"stub": True})
    return state
