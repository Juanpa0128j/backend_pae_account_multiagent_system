"""Unit tests for app/agents/audit_utils.py — append_finding routing."""

import pytest

from app.agents.audit_utils import append_audit_report, append_finding
from app.models.audit import AuditFinding, AuditReport, AuditTarget, Severity


def _make_state():
    return {
        "agent_log": [],
        "pipeline_warnings": [],
        "unfixable_findings": [],
    }


def _make_finding(severity=Severity.WARNING, fixable=False, rule_id="TEST-RULE"):
    return AuditFinding(
        target=AuditTarget.CONTADOR,
        rule_id=rule_id,
        severity=severity,
        fixable=fixable,
        responsible_agent="contador",
        technical_message="test technical message",
        user_message_es="mensaje de prueba",
    )


@pytest.mark.unit
class TestAppendFinding:
    def test_warning_goes_to_pipeline_warnings(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.WARNING))
        assert len(state["pipeline_warnings"]) == 1
        assert len(state["unfixable_findings"]) == 0

    def test_info_goes_to_pipeline_warnings(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.INFO))
        assert len(state["pipeline_warnings"]) == 1
        assert len(state["unfixable_findings"]) == 0

    def test_error_goes_to_pipeline_warnings(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.ERROR))
        assert len(state["pipeline_warnings"]) == 1
        assert len(state["unfixable_findings"]) == 0

    def test_blocker_not_fixable_goes_to_unfixable(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.BLOCKER, fixable=False))
        assert len(state["unfixable_findings"]) == 1
        assert len(state["pipeline_warnings"]) == 0

    def test_blocker_fixable_goes_to_pipeline_warnings(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.BLOCKER, fixable=True))
        assert len(state["pipeline_warnings"]) == 1
        assert len(state["unfixable_findings"]) == 0

    def test_always_emits_audit_finding_log_event(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.WARNING))
        assert len(state["agent_log"]) == 1
        assert state["agent_log"][0]["event"] == "audit_finding"

    def test_log_entry_has_correct_agent(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.WARNING))
        assert state["agent_log"][0]["agent"] == "contador"

    def test_log_entry_payload_contains_rule_id(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.WARNING, rule_id="CONT-RAG-MISS"))
        assert state["agent_log"][0]["details"]["rule_id"] == "CONT-RAG-MISS"

    def test_initialises_missing_buckets(self):
        state = {"agent_log": []}
        append_finding(state, _make_finding(Severity.WARNING))
        assert "pipeline_warnings" in state
        assert "unfixable_findings" in state

    def test_multiple_findings_accumulate(self):
        state = _make_state()
        append_finding(state, _make_finding(Severity.WARNING, rule_id="RULE-1"))
        append_finding(state, _make_finding(Severity.WARNING, rule_id="RULE-2"))
        assert len(state["pipeline_warnings"]) == 2
        assert len(state["agent_log"]) == 2

    def test_logs_under_current_node_agent_when_present(self):
        state = _make_state()
        state["current_agent"] = "ingesta"
        finding = AuditFinding(
            target=AuditTarget.INGEST,
            rule_id="ING-EXTRACTION-PARTIAL",
            severity=Severity.WARNING,
            fixable=False,
            responsible_agent="ingest",
            technical_message="partial extraction",
            user_message_es="mensaje de prueba",
        )
        append_finding(state, finding)
        assert state["agent_log"][0]["agent"] == "ingesta"


@pytest.mark.unit
class TestAppendAuditReport:
    def test_logs_report_under_current_node_agent(self):
        state = _make_state()
        state["current_agent"] = "db_persist"
        report = AuditReport(
            target=AuditTarget.PRE_PERSIST,
            approved=False,
            findings=[],
            attempt=1,
            duration_ms=5.0,
        )
        append_audit_report(state, report)
        assert state["agent_log"][0]["agent"] == "db_persist"
