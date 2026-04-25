"""Unit tests for app/agents/auditors/tributario_auditor.py"""

import pytest

from app.agents.auditors import tributario_auditor
from app.models.audit import AuditTarget, Severity


def _state(tributario_output=None):
    return {
        "agent_log": [],
        "pipeline_warnings": [],
        "unfixable_findings": [],
        "audit_reports": [],
        "tributario_output": tributario_output,
    }


@pytest.mark.unit
class TestTributarioAuditor:
    def test_empty_output_approved(self):
        state = _state(tributario_output=None)
        report = tributario_auditor.run(state)
        assert report.approved is True
        assert report.target == AuditTarget.TRIBUTARIO
        assert report.findings == []

    def test_no_impuestos_approved(self):
        state = _state(tributario_output={"aplica_impuestos": False, "impuestos": []})
        report = tributario_auditor.run(state)
        assert report.approved is True

    def test_valid_iva_19_approved(self):
        state = _state(
            tributario_output={"impuestos": [{"tipo": "iva", "tarifa": 0.19}]}
        )
        report = tributario_auditor.run(state)
        assert report.approved is True

    def test_valid_iva_5_approved(self):
        state = _state(
            tributario_output={"impuestos": [{"tipo": "iva", "tarifa": 0.05}]}
        )
        report = tributario_auditor.run(state)
        assert report.approved is True

    def test_valid_iva_0_approved(self):
        state = _state(tributario_output={"impuestos": [{"tipo": "iva", "tarifa": 0}]})
        report = tributario_auditor.run(state)
        assert report.approved is True

    def test_invalid_iva_rate_error(self):
        state = _state(
            tributario_output={"impuestos": [{"tipo": "iva", "tarifa": 0.12}]}
        )
        report = tributario_auditor.run(state)
        assert report.approved is False
        rule_ids = [f.rule_id for f in report.findings]
        assert "TRIB-IVA-RATE-INVALID" in rule_ids

    def test_invalid_iva_rate_is_fixable(self):
        state = _state(
            tributario_output={"impuestos": [{"tipo": "iva", "tarifa": 0.12}]}
        )
        report = tributario_auditor.run(state)
        finding = next(
            f for f in report.findings if f.rule_id == "TRIB-IVA-RATE-INVALID"
        )
        assert finding.severity == Severity.ERROR
        assert finding.fixable is True

    def test_non_iva_impuesto_skipped(self):
        state = _state(
            tributario_output={"impuestos": [{"tipo": "retefuente", "tarifa": 0.04}]}
        )
        report = tributario_auditor.run(state)
        assert report.approved is True
        assert report.findings == []

    def test_mixed_valid_invalid(self):
        state = _state(
            tributario_output={
                "impuestos": [
                    {"tipo": "iva", "tarifa": 0.19},
                    {"tipo": "iva", "tarifa": 0.10},
                ]
            }
        )
        report = tributario_auditor.run(state)
        assert report.approved is False
        assert (
            len([f for f in report.findings if f.rule_id == "TRIB-IVA-RATE-INVALID"])
            == 1
        )

    def test_evidence_contains_declared_rate(self):
        state = _state(
            tributario_output={"impuestos": [{"tipo": "iva", "tarifa": 0.15}]}
        )
        report = tributario_auditor.run(state)
        finding = next(
            f for f in report.findings if f.rule_id == "TRIB-IVA-RATE-INVALID"
        )
        assert "declared_rate" in finding.evidence

    def test_report_duration_and_attempt(self):
        state = _state(tributario_output={"impuestos": []})
        report = tributario_auditor.run(state, attempt=2)
        assert report.duration_ms >= 0
        assert report.attempt == 2
