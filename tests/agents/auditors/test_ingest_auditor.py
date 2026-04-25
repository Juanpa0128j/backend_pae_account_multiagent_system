"""Unit tests for app/agents/auditors/ingest_auditor.py"""

import pytest

from app.agents.auditors import ingest_auditor
from app.models.audit import AuditTarget, Severity


def _state(raw_text="", doc_type="factura_venta", interpreted=None):
    return {
        "agent_log": [],
        "pipeline_warnings": [],
        "unfixable_findings": [],
        "audit_reports": [],
        "raw_text": raw_text,
        "document_classification": {"doc_type": doc_type},
        "interpreted_data": (
            interpreted if interpreted is not None else {"campo": "valor"}
        ),
    }


@pytest.mark.unit
class TestIngestAuditor:
    def test_clean_state_approved(self):
        state = _state(raw_text="Factura de venta No. 1234 " * 5)
        report = ingest_auditor.run(state)
        assert report.approved is True
        assert report.target == AuditTarget.INGEST
        assert report.findings == []

    def test_empty_text_blocker(self):
        state = _state(raw_text="")
        report = ingest_auditor.run(state)
        assert report.approved is False
        rule_ids = [f.rule_id for f in report.findings]
        assert "ING-EMPTY-TEXT" in rule_ids

    def test_empty_text_is_not_fixable(self):
        state = _state(raw_text="")
        report = ingest_auditor.run(state)
        finding = next(f for f in report.findings if f.rule_id == "ING-EMPTY-TEXT")
        assert finding.severity == Severity.BLOCKER
        assert finding.fixable is False

    def test_short_text_warning(self):
        state = _state(raw_text="abc")
        report = ingest_auditor.run(state)
        rule_ids = [f.rule_id for f in report.findings]
        assert "ING-SHORT-TEXT" in rule_ids
        finding = next(f for f in report.findings if f.rule_id == "ING-SHORT-TEXT")
        assert finding.severity == Severity.WARNING

    def test_short_text_still_approved(self):
        state = _state(raw_text="abc")
        report = ingest_auditor.run(state)
        assert report.approved is True

    def test_unclassified_doc_warning(self):
        state = _state(raw_text="texto largo " * 10, doc_type="otro")
        report = ingest_auditor.run(state)
        rule_ids = [f.rule_id for f in report.findings]
        assert "ING-UNCLASSIFIED-DOC" in rule_ids

    def test_empty_doc_type_warning(self):
        state = _state(raw_text="texto largo " * 10, doc_type="")
        report = ingest_auditor.run(state)
        rule_ids = [f.rule_id for f in report.findings]
        assert "ING-UNCLASSIFIED-DOC" in rule_ids

    def test_no_interpreted_data_error(self):
        state = _state(raw_text="texto largo " * 10, interpreted={})
        report = ingest_auditor.run(state)
        rule_ids = [f.rule_id for f in report.findings]
        assert "ING-NO-INTERPRETED-DATA" in rule_ids
        finding = next(
            f for f in report.findings if f.rule_id == "ING-NO-INTERPRETED-DATA"
        )
        assert finding.severity == Severity.ERROR
        assert finding.fixable is True
        assert report.approved is False

    def test_no_interpreted_data_skipped_if_empty_text(self):
        state = _state(raw_text="", interpreted={})
        report = ingest_auditor.run(state)
        rule_ids = [f.rule_id for f in report.findings]
        assert "ING-NO-INTERPRETED-DATA" not in rule_ids

    def test_report_has_duration(self):
        state = _state(raw_text="texto " * 20)
        report = ingest_auditor.run(state)
        assert report.duration_ms >= 0

    def test_attempt_passed_through(self):
        state = _state(raw_text="texto " * 20)
        report = ingest_auditor.run(state, attempt=3)
        assert report.attempt == 3
