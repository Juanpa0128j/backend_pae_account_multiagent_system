"""
Contador worker node for the process pipeline.

Consumes staged transactions and produces ContadorOutput-compatible JSON.
"""

import logging

from app.agents.state import AgentState
from app.core.gemini_client import get_gemini_client

logger = logging.getLogger(__name__)


def contador_node(state: AgentState) -> AgentState:
    """
    Build accounting classification and journal entries from staged transactions.
    """
    if state.get("error"):
        logger.warning("Contador: skipping due to upstream error")
        return state

    raw_txs = state.get("raw_transactions", [])
    if not raw_txs:
        state["error"] = "Contador error: no staged transactions provided"
        return state

    correction_feedback = state.get("correction_feedback")

    try:
        state["current_agent"] = "contador"
        state["current_stage"] = "classifying"

        gemini_client = get_gemini_client()
        output = gemini_client.extract_contador_output(
            raw_txs,
            correction_feedback=correction_feedback,
        )

        state["contador_output"] = output
        state["interpreted_data"] = output
        state["correction_feedback"] = None
        state["agent_log"] = state.get("agent_log", []) + [
            {
                "agent": "contador",
                "stage": "classifying",
                "status": "completed",
            }
        ]

        logger.info("Contador: output generated")
        return state
    except Exception as e:
        state["error"] = f"Contador error: {str(e)}"
        logger.error(state["error"], exc_info=True)
        return state
