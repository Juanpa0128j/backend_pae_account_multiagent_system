"""
Derives a PipelineTrace from a ProcessJob's agent_log.

Read-only — never commits to DB. Called by GET /api/v1/process/{id}/trace.

Trace derivation strategy (Phase 1):
- Group agent_log entries by agent name in chronological order.
- Each unique agent appearance (consecutive run) becomes a TraceStep.
- Status is derived from log events within the run.
- Phase 2+ audit_finding entries in agent_log will enrich the details_es list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.audit import AuditFinding, GiveUpRecord
from app.models.database import ProcessStatus
from app.models.trace import PipelineTrace, TraceStep
from app.services import db_service
from app.services.audit_messages_es import get_agent_summary_es, get_message

logger = logging.getLogger(__name__)

_FAILURE_EVENTS = frozenset(
    {"node_failed", "node_error", "error", "agent_error", "validation_failed_final"}
)
_WARNING_EVENTS = frozenset({"audit_finding", "warning", "non_fatal_error"})
_RETRY_EVENTS = frozenset({"retry", "correction_applied", "validation_retry"})


def _parse_ts(ts_str: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _derive_step_status(events: list[str], has_findings: bool) -> str:
    if any(e in _FAILURE_EVENTS for e in events):
        return "failed"
    if any(e in _RETRY_EVENTS for e in events):
        return "retried"
    if has_findings or any(e in _WARNING_EVENTS for e in events):
        return "warning"
    return "ok"


def _extract_findings_from_log(log_entries: list[dict]) -> list[AuditFinding]:
    """Extract AuditFinding objects stored as audit_finding log events (Phase 2+)."""
    findings: list[AuditFinding] = []
    for entry in log_entries:
        if entry.get("event") != "audit_finding":
            continue
        details = entry.get("details", {})
        try:
            findings.append(AuditFinding.model_validate(details))
        except Exception:
            logger.debug(
                "pipeline_trace: could not parse audit_finding entry: %r", details
            )
    return findings


def _extract_giveup_from_log(log_entries: list[dict]) -> Optional[GiveUpRecord]:
    """Extract GiveUpRecord stored as give-up log event (Phase 4+)."""
    for entry in reversed(log_entries):
        if entry.get("event") in {"give_up", "audit_giveup"}:
            try:
                return GiveUpRecord.model_validate(entry.get("details", {}))
            except Exception:
                logger.debug("pipeline_trace: could not parse give_up entry")
    return None


def build_trace(process_id: str, db: Session) -> Optional[PipelineTrace]:
    """Build a PipelineTrace from a ProcessJob's agent_log.

    Returns None if the process_job is not found.
    Never raises — logs errors and returns a best-effort trace.
    """
    process_job = db_service.get_process_job(db, process_id)
    if not process_job:
        return None

    raw_log: list[dict] = process_job.agent_log or []

    overall_status: str
    if process_job.status == ProcessStatus.FAILED:
        overall_status = "failed"
    else:
        overall_status = "completed"

    # Group log entries into consecutive runs per agent
    runs: list[tuple[str, list[dict]]] = []
    for entry in raw_log:
        agent = entry.get("agent", "unknown")
        if runs and runs[-1][0] == agent:
            runs[-1][1].append(entry)
        else:
            runs.append((agent, [entry]))

    all_findings: list[AuditFinding] = _extract_findings_from_log(raw_log)
    blockers: list[AuditFinding] = [
        f for f in all_findings if f.severity.value == "blocker" and not f.fixable
    ]

    if blockers:
        overall_status = "failed"
    elif all_findings and overall_status == "completed":
        overall_status = "completed_with_warnings"

    # Pre-compute per-run start times so single-entry runs can borrow the
    # next run's start as their ended_at instead of producing 0ms durations.
    run_start_times: list[datetime] = []
    for _, entries in runs:
        if entries:
            run_start_times.append(
                min(_parse_ts(e.get("timestamp", "")) for e in entries)
            )
        else:
            run_start_times.append(datetime.now(timezone.utc))

    steps: list[TraceStep] = []
    for run_idx, (agent, entries) in enumerate(runs):
        if not entries:
            continue

        timestamps = [_parse_ts(e.get("timestamp", "")) for e in entries]
        started_at = min(timestamps)
        ended_at = max(timestamps)
        # Single-entry run: borrow the next run's started_at as ended_at
        if started_at == ended_at and run_idx + 1 < len(run_start_times):
            ended_at = run_start_times[run_idx + 1]

        events = [e.get("event", "") for e in entries]
        run_finding_payloads = [
            entry.get("details", {})
            for entry in entries
            if entry.get("event") == "audit_finding"
        ]
        run_findings: list[AuditFinding] = []
        for payload in run_finding_payloads:
            try:
                run_findings.append(AuditFinding.model_validate(payload))
            except Exception:
                logger.debug(
                    "pipeline_trace: could not parse run audit_finding entry: %r",
                    payload,
                )
        status = _derive_step_status(events, bool(run_findings))

        summary_es = get_agent_summary_es(agent, failed=(status == "failed"))

        details_es: list[str] = []
        suggested_action_es: Optional[str] = None
        for finding in run_findings:
            msg = finding.user_message_es
            action = finding.suggested_action_es

            if not msg or not action:
                fallback_msg, fallback_action = get_message(
                    finding.rule_id, finding.evidence
                )
                if not msg:
                    msg = fallback_msg
                if not action:
                    action = fallback_action

            details_es.append(msg)
            if action and suggested_action_es is None:
                suggested_action_es = action

        steps.append(
            TraceStep(
                agent=agent,
                started_at=started_at,
                ended_at=ended_at,
                status=status,
                summary_es=summary_es,
                details_es=details_es,
                suggested_action_es=suggested_action_es,
                findings=run_findings,
                technical_ref=f"run-{run_idx}-{agent}",
            )
        )

    give_up = _extract_giveup_from_log(raw_log)

    return PipelineTrace(
        process_id=process_id,
        overall_status=overall_status,
        steps=steps,
        blockers=blockers,
        give_up=give_up,
    )


def build_ingest_trace(ingest_id: str, db: Session) -> Optional[PipelineTrace]:
    """Build a PipelineTrace from an IngestJob's AuditLog entries.

    Returns None if the ingest_job is not found.
    Returns None for non-terminal states (PENDING_PROCESSING, PROCESSING)
    so the endpoint can return 409 Conflict.
    Never raises — returns a best-effort trace.
    """
    from app.models.database import AuditLog, IngestStatus

    ingest_job = db_service.get_ingest_job(db, ingest_id)
    if not ingest_job:
        return None

    # Only return traces for terminal states
    if ingest_job.status not in (IngestStatus.COMPLETED, IngestStatus.FAILED):
        return None

    # Query audit logs directly related to this ingest OR related via details->>ingest_id
    audit_logs = (
        db.query(AuditLog)
        .filter(
            (AuditLog.entity_id == ingest_id)
            | (AuditLog.details["ingest_id"].as_string() == ingest_id)
        )
        .order_by(AuditLog.created_at.asc())
        .all()
    )

    # Handle terminal statuses
    if ingest_job.status == IngestStatus.FAILED:
        overall_status = "failed"
    else:
        overall_status = "completed"

    steps: list[TraceStep] = []
    total = len(audit_logs)
    # Collect timestamps up-front so each step's ended_at = next step's started_at
    log_timestamps = [
        (log.created_at or datetime.now(timezone.utc)) for log in audit_logs
    ]
    for idx, log in enumerate(audit_logs):
        details = log.details or {}
        agent = details.get("agent", "ingesta")
        is_last_and_failed = overall_status == "failed" and idx == total - 1
        step_status = "failed" if is_last_and_failed else "ok"
        detail_text = details.get("message") or details.get("summary") or ""
        started_at = log_timestamps[idx]
        ended_at = log_timestamps[idx + 1] if idx + 1 < total else started_at
        steps.append(
            TraceStep(
                agent=agent,
                started_at=started_at,
                ended_at=ended_at,
                status=step_status,
                summary_es=get_agent_summary_es(agent, failed=is_last_and_failed),
                details_es=[detail_text] if detail_text else [],
                suggested_action_es=details.get("suggested_action_es"),
                technical_ref=f"audit-log-{log.id}",
            )
        )

    blockers: list[AuditFinding] = []
    if ingest_job.extraction_errors:
        from app.models.audit import AuditTarget, Severity

        msg, action = get_message("INGEST_ERROR", {})
        for err in ingest_job.extraction_errors:
            err_str = str(err) or msg
            blockers.append(
                AuditFinding(
                    target=AuditTarget.INGEST,
                    rule_id="INGEST_ERROR",
                    severity=Severity.BLOCKER,
                    fixable=False,
                    responsible_agent="ingest",
                    technical_message=err_str,
                    user_message_es=err_str,
                    suggested_action_es=action,
                    evidence={"ingest_id": ingest_id},
                )
            )

    return PipelineTrace(
        process_id=ingest_id,
        overall_status=overall_status,
        steps=steps,
        blockers=blockers,
        give_up=None,
    )
