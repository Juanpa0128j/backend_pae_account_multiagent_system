"""
Backward-compatible re-exports for app.agents.supervisor.

Routing logic has moved to:
  app/agents/routing/ingest_router.py
  app/agents/routing/process_router.py
  app/agents/routing/terminal_nodes.py
  app/agents/routing/edge_functions.py
  app/services/tributario_normalizer.py

supervisor_node remains here as a full mode dispatcher so existing tests
that patch app.agents.supervisor.SessionLocal, db_service, validate_auditor_output_node,
etc. continue to work without modification.
"""

from app.agents.agent_utils import append_log  # noqa: F401
from app.agents.routing.edge_functions import (  # noqa: F401
    MAX_AUDITOR_RETRIES,
    MAX_CONTADOR_RETRIES,
    route_after_supervisor,
    should_retry_agent,
    should_retry_auditor,
    should_retry_contador,
)
from app.agents.routing.ingest_router import route_ingest  # noqa: F401
from app.agents.routing.process_router import (  # noqa: F401
    process_supervisor_node,
    route_process,
)
from app.agents.routing.terminal_nodes import (  # noqa: F401
    audit_review_terminal_node,
    error_terminal_node,
    review_terminal_node,
)
from app.agents.state import AgentState
from app.agents.validation_rules import (  # noqa: F401
    GLOBAL_AUDIT_FAILURES,
    RETRY_BUDGETS,
    SINGLE_PASS_DOC_TYPES,
    _hydrate_contador_account_names,
    _missing_puc_codes,
    _normalize_contador_puc_codes,
    _resolve_puc_code,
    validate_auditor_output_node,
    validate_contador_output_node,
    validate_output_node,
)
from app.core.database import SessionLocal  # noqa: F401
from app.core.logger import get_logger
from app.services import db_service  # noqa: F401
from app.services.tributario_normalizer import (  # noqa: F401
    normalize_tributario_output as _normalize_tributario_output,
)

logger = get_logger("app.agents.supervisor")


def supervisor_node(state: AgentState) -> AgentState:
    """Mode dispatcher — delegates to route_ingest or route_process based on mode."""
    if state.get("agent_log") is None:
        state["agent_log"] = []
    append_log(
        state,
        "supervisor",
        "routing_start",
        {
            "mode": state.get("mode", "ingest"),
            "current_agent": state.get("current_agent", ""),
        },
    )
    if state.get("mode") == "reporting":
        # Reporting pipeline jumps straight to reportero node — no
        # ingest validation, no contador/tributario/auditor loop. Lost
        # during PR #72 routing extraction; restoring here.
        state["current_agent"] = "reportero"
        return state
    if state.get("mode") == "process":
        return route_process(state)
    return route_ingest(state)
