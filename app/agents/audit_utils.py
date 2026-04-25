"""Shared helpers for emitting AuditFinding objects into agent state."""

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.models.audit import AuditFinding, Severity


def append_finding(state: AgentState, finding: AuditFinding) -> None:
    """Route a finding into the appropriate state bucket and emit an audit_finding log event.

    Routing:
    - BLOCKER + not fixable → unfixable_findings (blocks persist in Phase 5)
    - Everything else → pipeline_warnings
    Always emits an audit_finding log entry so the Phase 1 trace endpoint can read it.
    """
    if state.get("pipeline_warnings") is None:
        state["pipeline_warnings"] = []
    if state.get("unfixable_findings") is None:
        state["unfixable_findings"] = []

    payload = finding.model_dump()
    if finding.severity == Severity.BLOCKER and not finding.fixable:
        state["unfixable_findings"].append(payload)
    else:
        state["pipeline_warnings"].append(payload)

    append_log(state, finding.responsible_agent, "audit_finding", payload)
