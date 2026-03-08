"""
Agente Tributario (Tax Specialist)

Role (docs/Diseño de arquitectura de agente):
  - Receives classified Transaction from Contador.
  - Calculates Retefuente, ReteICA, IVA using search_tax_law RAG + tax_calculator.
  - Returns Transaction enriched with tax amounts and liability accounts (2365, 2408).
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

logger = logging.getLogger(__name__)


def tributario_node(state: AgentState) -> AgentState:
    """
    Tributario stub node — no-op until Sprint 12.
    Sets current_agent so supervisor can advance the pipeline.
    """
    if state.get("error"):
        return state
    append_log(state, "tributario", "node_start", {"stub": True, "sprint": 12})
    logger.info("Tributario: STUB — no-op until Sprint 12")
    state["current_agent"] = "tributario"
    state["current_stage"] = "tributario_complete"
    append_log(state, "tributario", "node_complete", {"stub": True})
    return state
