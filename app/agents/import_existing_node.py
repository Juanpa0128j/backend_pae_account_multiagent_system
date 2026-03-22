"""
Import Existing node — Vía B of the ingestion pipeline.

Handles documents that are already financial statements (balance general,
estado de resultados, libro auxiliar). These skip the full accounting
pipeline (contador→tributario→auditor) and are stored directly for
reporting purposes.
"""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState

logger = logging.getLogger(__name__)


def import_existing_node(state: AgentState) -> AgentState:
    """
    Import an existing financial statement for reporting.

    Reads:
        state["interpreted_data"] — parsed financial statement data
        state["document_classification"] — classification metadata
    Writes:
        state["result"] — success result with parsed data
        state["current_stage"] — "import_complete"
        state["current_agent"] — "import_existing"
    """
    if state.get("error"):
        logger.warning(
            "import_existing: skipping due to upstream error: %s", state["error"]
        )
        return state

    classification = state.get("document_classification") or {}
    interpreted = state.get("interpreted_data") or {}
    doc_type = classification.get("doc_type", "unknown")

    append_log(state, "import_existing", "node_start", {
        "doc_type": doc_type,
        "pathway": "work_with_existing",
    })

    if not interpreted:
        state["error"] = "import_existing: no interpreted data to import"
        logger.error(state["error"])
        append_log(state, "import_existing", "node_error", {
            "error": state["error"],
        })
        return state

    state["result"] = {
        "status": "completed",
        "pathway": "work_with_existing",
        "doc_type": doc_type,
        "data": interpreted,
        "message": f"Financial statement ({doc_type}) imported successfully",
    }
    state["current_stage"] = "import_complete"
    state["current_agent"] = "import_existing"

    append_log(state, "import_existing", "node_complete", {
        "doc_type": doc_type,
    })
    logger.info("import_existing: %s imported successfully", doc_type)
    return state
