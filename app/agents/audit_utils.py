"""Shared helpers for emitting AuditFinding objects into agent state."""

import logging

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.models.audit import AuditFinding, AuditReport, GiveUpRecord, Severity

logger = logging.getLogger(__name__)


_TARGET_TO_NODE_AGENT: dict[str, str] = {
    "ingest": "ingesta",
    "contador": "contador",
    "tributario": "tributario",
    "pre_persist": "db_persist",
    "persist": "db_persist",
}


def _log_agent_for_state(state: AgentState, fallback: str) -> str:
    """Return the node agent name to use in agent_log for audit events."""
    current_agent = str(state.get("current_agent") or "").strip()
    if current_agent:
        return current_agent
    return _TARGET_TO_NODE_AGENT.get(fallback, fallback)


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

    append_log(
        state,
        _log_agent_for_state(state, finding.responsible_agent),
        "audit_finding",
        payload,
    )


def append_audit_report(state: AgentState, report: AuditReport) -> None:
    """Append an AuditReport to state["audit_reports"] and emit a log event.

    Also fans out each finding in the report through append_finding so that
    findings appear in pipeline_warnings / unfixable_findings buckets.
    """
    if state.get("audit_reports") is None:
        state["audit_reports"] = []

    payload = report.model_dump()
    state["audit_reports"].append(payload)

    append_log(
        state,
        _log_agent_for_state(state, report.target.value),
        "audit_report",
        {
            "target": report.target.value,
            "approved": report.approved,
            "finding_count": len(report.findings),
            "attempt": report.attempt,
            "duration_ms": round(report.duration_ms, 1),
        },
    )

    for finding in report.findings:
        append_finding(state, finding)


def build_pinpointed_prompt(findings: list[AuditFinding]) -> str:
    """Render fixable findings into a structured correction prompt for the responsible agent.

    Each finding block includes rule_id, technical description, supporting evidence,
    and a suggested action so the LLM knows exactly what to fix.
    """
    lines: list[str] = [
        "Los siguientes problemas fueron detectados por el auditor. Corrígelos en tu próxima respuesta:",
        "",
    ]
    for i, f in enumerate(findings, start=1):
        lines.append(f"[{i}] {f.rule_id}: {f.technical_message}")
        if f.evidence:
            evidence_str = ", ".join(f"{k}={v}" for k, v in f.evidence.items())
            lines.append(f"    Evidencia: {evidence_str}")
        if f.suggested_action_es:
            lines.append(f"    Acción: {f.suggested_action_es}")
        lines.append("")
    return "\n".join(lines)


def record_giveup(
    state: AgentState,
    target: str,
    findings: list[AuditFinding],
) -> None:
    """Record a GiveUpRecord in state when the retry budget for an agent is exhausted.

    Sets state["giveup_record"] and appends the explanation to agent_log.
    """
    attempts = sum(
        1
        for r in (state.get("audit_reports") or [])
        if r.get("target") == target and not r.get("approved", True)
    )
    finding_count = len(findings)
    rule_ids = (
        ", ".join(f.rule_id for f in findings)
        if findings
        else "sin hallazgos específicos"
    )

    explanation_es = (
        f"El sistema intentó corregir automáticamente el agente '{target}' {attempts} veces "
        f"pero no logró resolver los problemas detectados ({rule_ids}). "
        "Se requiere revisión manual por parte del contador."
    )

    record = GiveUpRecord(
        target=target,  # type: ignore[arg-type]
        attempts=max(attempts, 1),
        last_findings=findings,
        explanation_es=explanation_es,
    )
    state["giveup_record"] = record.model_dump()

    logger.warning(
        "audit: give-up recorded for target=%s findings=%d rule_ids=%s",
        target,
        finding_count,
        rule_ids,
    )
    append_log(
        state,
        _log_agent_for_state(state, target),
        "audit_giveup",
        record.model_dump(),
    )
