"""Unit tests for app/services/pipeline_trace_service.py."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.database import ProcessStatus
from app.services.pipeline_trace_service import (
    _derive_step_status,
    _extract_findings_from_log,
    _extract_giveup_from_log,
    _parse_ts,
    build_trace,
)


def _make_process_job(status=ProcessStatus.COMPLETED, agent_log=None):
    job = MagicMock()
    job.id = "proc-test-001"
    job.status = status
    job.agent_log = agent_log or []
    return job


def _log_entry(
    agent: str,
    event: str,
    ts: str = "2026-04-25T12:00:00+00:00",
    details: dict | None = None,
):
    return {"timestamp": ts, "agent": agent, "event": event, "details": details or {}}


@pytest.mark.unit
class TestParseTsHelper:
    def test_iso_utc_string(self):
        dt = _parse_ts("2026-04-25T12:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_naive_string_gets_utc(self):
        dt = _parse_ts("2026-04-25T12:00:00")
        assert dt.tzinfo == timezone.utc

    def test_invalid_returns_now(self):
        dt = _parse_ts("not-a-date")
        assert isinstance(dt, datetime)


@pytest.mark.unit
class TestDeriveStepStatus:
    def test_failure_event_dominates(self):
        assert _derive_step_status(["node_start", "node_failed"], False) == "failed"

    def test_node_error_counts_as_failed(self):
        assert _derive_step_status(["node_start", "node_error"], False) == "failed"

    def test_retry_event(self):
        assert (
            _derive_step_status(["node_start", "validation_retry"], False) == "retried"
        )

    def test_warning_via_findings(self):
        assert _derive_step_status(["node_start", "node_complete"], True) == "warning"

    def test_warning_via_event(self):
        assert _derive_step_status(["audit_finding"], False) == "warning"

    def test_ok(self):
        assert _derive_step_status(["node_start", "node_complete"], False) == "ok"


@pytest.mark.unit
class TestExtractFindingsFromLog:
    def test_empty_log(self):
        assert _extract_findings_from_log([]) == []

    def test_non_finding_entries_ignored(self):
        log = [_log_entry("contador", "node_start")]
        assert _extract_findings_from_log(log) == []

    def test_valid_finding_parsed(self):
        finding_details = {
            "target": "contador",
            "rule_id": "CONT-RAG-MISS",
            "severity": "warning",
            "fixable": False,
            "responsible_agent": "contador",
            "technical_message": "RAG lookup failed",
            "user_message_es": "No se encontró la cuenta.",
        }
        log = [
            {
                "timestamp": "2026-04-25T12:00:00+00:00",
                "agent": "contador",
                "event": "audit_finding",
                "details": finding_details,
            }
        ]
        findings = _extract_findings_from_log(log)
        assert len(findings) == 1
        assert findings[0].rule_id == "CONT-RAG-MISS"

    def test_malformed_finding_skipped(self):
        log = [
            {
                "timestamp": "2026-04-25T12:00:00+00:00",
                "agent": "x",
                "event": "audit_finding",
                "details": {"bad": "data"},
            }
        ]
        findings = _extract_findings_from_log(log)
        assert findings == []


@pytest.mark.unit
class TestExtractGiveupFromLog:
    def test_empty_log(self):
        assert _extract_giveup_from_log([]) is None

    def test_give_up_entry_parsed(self):
        giveup_details = {
            "target": "contador",
            "attempts": 2,
            "last_findings": [],
            "explanation_es": "No se pudo corregir.",
        }
        log = [
            {
                "timestamp": "2026-04-25T12:00:00+00:00",
                "agent": "supervisor",
                "event": "give_up",
                "details": giveup_details,
            }
        ]
        record = _extract_giveup_from_log(log)
        assert record is not None
        assert record.target == "contador"
        assert record.attempts == 2

    def test_audit_giveup_entry_parsed(self):
        giveup_details = {
            "target": "contador",
            "attempts": 2,
            "last_findings": [],
            "explanation_es": "No se pudo corregir.",
        }
        log = [
            {
                "timestamp": "2026-04-25T12:00:00+00:00",
                "agent": "contador",
                "event": "audit_giveup",
                "details": giveup_details,
            }
        ]
        record = _extract_giveup_from_log(log)
        assert record is not None
        assert record.target == "contador"
        assert record.attempts == 2


@pytest.mark.unit
class TestBuildTrace:
    def test_returns_none_when_job_not_found(self):
        db = MagicMock()
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = None
            result = build_trace("nonexistent", db)
        assert result is None

    def test_empty_log_completed(self):
        db = MagicMock()
        job = _make_process_job(ProcessStatus.COMPLETED, agent_log=[])
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert trace is not None
        assert trace.overall_status == "completed"
        assert trace.steps == []
        assert trace.blockers == []

    def test_failed_job_gives_failed_status(self):
        db = MagicMock()
        job = _make_process_job(ProcessStatus.FAILED, agent_log=[])
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert trace.overall_status == "failed"

    def test_single_agent_step_derived(self):
        db = MagicMock()
        log = [
            _log_entry("contador", "node_start", "2026-04-25T12:00:00+00:00"),
            _log_entry("contador", "node_complete", "2026-04-25T12:00:03+00:00"),
        ]
        job = _make_process_job(ProcessStatus.COMPLETED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert len(trace.steps) == 1
        step = trace.steps[0]
        assert step.agent == "contador"
        assert step.status == "ok"
        assert "contador" in step.summary_es.lower()

    def test_multiple_agents_produce_multiple_steps(self):
        db = MagicMock()
        log = [
            _log_entry("ingesta", "node_start", "2026-04-25T12:00:00+00:00"),
            _log_entry("ingesta", "node_complete", "2026-04-25T12:00:01+00:00"),
            _log_entry("contador", "node_start", "2026-04-25T12:00:02+00:00"),
            _log_entry("contador", "node_complete", "2026-04-25T12:00:05+00:00"),
        ]
        job = _make_process_job(ProcessStatus.COMPLETED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert len(trace.steps) == 2
        assert trace.steps[0].agent == "ingesta"
        assert trace.steps[1].agent == "contador"

    def test_failure_event_sets_failed_step(self):
        db = MagicMock()
        log = [
            _log_entry("contador", "node_start", "2026-04-25T12:00:00+00:00"),
            _log_entry("contador", "node_failed", "2026-04-25T12:00:02+00:00"),
        ]
        job = _make_process_job(ProcessStatus.FAILED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert trace.steps[0].status == "failed"

    def test_blocker_finding_sets_failed_overall(self):
        db = MagicMock()
        finding_details = {
            "target": "contador",
            "rule_id": "PERS-DOUBLE-ENTRY-FAIL",
            "severity": "blocker",
            "fixable": False,
            "responsible_agent": "contador",
            "technical_message": "debits != credits",
            "user_message_es": "Los asientos no están balanceados.",
            "evidence": {"total_debits": "1000", "total_credits": "900"},
        }
        log = [
            _log_entry("contador", "node_start", "2026-04-25T12:00:00+00:00"),
            {
                "timestamp": "2026-04-25T12:00:01+00:00",
                "agent": "contador",
                "event": "audit_finding",
                "details": finding_details,
            },
        ]
        job = _make_process_job(ProcessStatus.COMPLETED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert trace.overall_status == "failed"
        assert len(trace.blockers) == 1

    def test_warning_finding_sets_completed_with_warnings(self):
        db = MagicMock()
        finding_details = {
            "target": "contador",
            "rule_id": "CONT-RAG-MISS",
            "severity": "warning",
            "fixable": False,
            "responsible_agent": "contador",
            "technical_message": "RAG miss",
            "user_message_es": "No se encontró la cuenta.",
        }
        log = [
            _log_entry("contador", "node_start", "2026-04-25T12:00:00+00:00"),
            {
                "timestamp": "2026-04-25T12:00:01+00:00",
                "agent": "contador",
                "event": "audit_finding",
                "details": finding_details,
            },
        ]
        job = _make_process_job(ProcessStatus.COMPLETED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)
        assert trace.overall_status == "completed_with_warnings"

    def test_db_persist_step_includes_persist_findings(self):
        db = MagicMock()
        finding_details = {
            "target": "pre_persist",
            "rule_id": "PERS-DOUBLE-ENTRY-FAIL",
            "severity": "blocker",
            "fixable": False,
            "responsible_agent": "persist",
            "technical_message": "debits != credits",
            "user_message_es": "Los asientos no están balanceados.",
            "suggested_action_es": "Corrija el asiento antes de persistir.",
            "evidence": {"total_debits": "1000", "total_credits": "900"},
        }
        log = [
            _log_entry("db_persist", "node_start", "2026-04-25T12:00:00+00:00"),
            {
                "timestamp": "2026-04-25T12:00:01+00:00",
                "agent": "db_persist",
                "event": "audit_finding",
                "details": finding_details,
            },
            _log_entry("db_persist", "node_failed", "2026-04-25T12:00:02+00:00"),
        ]
        job = _make_process_job(ProcessStatus.FAILED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)

        persist_step = next(step for step in trace.steps if step.agent == "db_persist")
        assert len(persist_step.details_es) == 1
        assert "no están balanceados" in persist_step.details_es[0]
        assert persist_step.suggested_action_es
        assert "desbalance" in persist_step.suggested_action_es.lower()

    def test_fixable_blocker_does_not_force_failed_overall(self):
        db = MagicMock()
        finding_details = {
            "target": "contador",
            "rule_id": "PERS-DOUBLE-ENTRY-FAIL",
            "severity": "blocker",
            "fixable": True,
            "responsible_agent": "contador",
            "technical_message": "debits != credits",
            "user_message_es": "Los asientos no están balanceados.",
            "evidence": {"total_debits": "1000", "total_credits": "900"},
        }
        log = [
            _log_entry("contador", "node_start", "2026-04-25T12:00:00+00:00"),
            {
                "timestamp": "2026-04-25T12:00:01+00:00",
                "agent": "contador",
                "event": "audit_finding",
                "details": finding_details,
            },
            _log_entry("contador", "node_complete", "2026-04-25T12:00:02+00:00"),
        ]
        job = _make_process_job(ProcessStatus.COMPLETED, agent_log=log)
        with patch("app.services.pipeline_trace_service.db_service") as mock_svc:
            mock_svc.get_process_job.return_value = job
            trace = build_trace("proc-test-001", db)

        assert trace.overall_status == "completed_with_warnings"
        assert trace.blockers == []
