"""Supervisor node and related functions for the agent graph.

Backward-compat re-export module. All implementations moved to
app/agents/routing/ submodules.
"""

from app.agents.routing.edge_functions import (
    should_retry_agent,
    should_retry_contador,
    should_retry_auditor,
)
from app.agents.routing.supervisor_node import (
    process_supervisor_node,
    route_after_supervisor,
    supervisor_node,
)
from app.agents.routing.terminal_nodes import (
    audit_review_terminal_node,
    error_terminal_node,
    review_terminal_node,
)
from app.agents.validation_rules import (
    MAX_AUDITOR_RETRIES,
    MAX_CONTADOR_RETRIES,
    _hydrate_contador_account_names,
    _missing_puc_codes,
    _normalize_contador_puc_codes,
    _resolve_puc_code,
    validate_auditor_output_node,
    validate_contador_output_node,
    validate_output_node,
)

__all__ = [
    "MAX_AUDITOR_RETRIES",
    "MAX_CONTADOR_RETRIES",
    "_hydrate_contador_account_names",
    "_missing_puc_codes",
    "_normalize_contador_puc_codes",
    "_resolve_puc_code",
    "audit_review_terminal_node",
    "error_terminal_node",
    "review_terminal_node",
    "process_supervisor_node",
    "route_after_supervisor",
    "should_retry_agent",
    "should_retry_auditor",
    "should_retry_contador",
    "supervisor_node",
    "validate_auditor_output_node",
    "validate_contador_output_node",
    "validate_output_node",
]
