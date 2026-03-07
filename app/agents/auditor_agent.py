"""
Agente Auditor (Internal Control) — STUB for Sprint 9.
Full implementation: Sprint 13.

Role (docs/Diseño de arquitectura de agente):
  - Validates double-entry integrity: sum(debits) == sum(credits).
  - Detects anomalies and duplicate invoices.
  - Sets audit_decision = "approved" | "rejected" and audit_feedback on rejection.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

logger = logging.getLogger(__name__)


def auditor_node(state: AgentState) -> AgentState:
    """
    Auditor stub node — auto-approves all transactions until Sprint 13.
    """
    if state.get("error"):
        return state
    append_log(state, "auditor", "node_start", {"stub": True, "sprint": 13})
    logger.info("Auditor: STUB — auto-approving until Sprint 13")
    # Stub always approves — real double-entry validation in Sprint 13
    state["audit_decision"] = "approved"
    state["audit_feedback"] = None
    state["current_agent"] = "auditor"
    state["current_stage"] = "audit_complete"
    append_log(state, "auditor", "node_complete", {"decision": "approved", "stub": True})
    return state
