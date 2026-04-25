"""Unit tests for app/agents/auditors/contador_auditor.py"""

import pytest

from app.agents.auditors import contador_auditor
from app.models.audit import AuditTarget, Severity


def _state(asientos=None):
    return {
        "agent_log": [],
        "pipeline_warnings": [],
        "unfixable_findings": [],
        "audit_reports": [],
        "contador_output": {"asientos": asientos} if asientos is not None else {},
    }


def _balanced_asientos():
    return [
        {"tipo_movimiento": "debito", "valor": "1000.00", "cuenta_puc": "511005"},
        {"tipo_movimiento": "credito", "valor": "1000.00", "cuenta_puc": "110505"},
    ]


@pytest.mark.unit
class TestContadorAuditor:
    def test_balanced_approved(self):
        state = _state(asientos=_balanced_asientos())
        report = contador_auditor.run(state)
        assert report.approved is True
        assert report.target == AuditTarget.CONTADOR
        assert report.findings == []

    def test_empty_asientos_blocker(self):
        state = _state(asientos=[])
        report = contador_auditor.run(state)
        assert report.approved is False
        rule_ids = [f.rule_id for f in report.findings]
        assert "CONT-EMPTY-ASIENTOS" in rule_ids

    def test_empty_asientos_is_fixable(self):
        state = _state(asientos=[])
        report = contador_auditor.run(state)
        finding = next(f for f in report.findings if f.rule_id == "CONT-EMPTY-ASIENTOS")
        assert finding.severity == Severity.BLOCKER
        assert finding.fixable is True

    def test_missing_contador_output_blocker(self):
        state = _state()
        report = contador_auditor.run(state)
        assert report.approved is False

    def test_unbalanced_mismatch_blocker(self):
        asientos = [
            {"tipo_movimiento": "debito", "valor": "1000.00", "cuenta_puc": "511005"},
            {"tipo_movimiento": "credito", "valor": "900.00", "cuenta_puc": "110505"},
        ]
        state = _state(asientos=asientos)
        report = contador_auditor.run(state)
        assert report.approved is False
        rule_ids = [f.rule_id for f in report.findings]
        assert "CONT-BALANCE-MISMATCH" in rule_ids

    def test_balance_mismatch_is_fixable(self):
        asientos = [
            {"tipo_movimiento": "debito", "valor": "500.00", "cuenta_puc": "511005"},
            {"tipo_movimiento": "credito", "valor": "400.00", "cuenta_puc": "110505"},
        ]
        state = _state(asientos=asientos)
        report = contador_auditor.run(state)
        finding = next(
            f for f in report.findings if f.rule_id == "CONT-BALANCE-MISMATCH"
        )
        assert finding.severity == Severity.BLOCKER
        assert finding.fixable is True

    def test_balance_within_tolerance_approved(self):
        asientos = [
            {"tipo_movimiento": "debito", "valor": "1000.005", "cuenta_puc": "511005"},
            {"tipo_movimiento": "credito", "valor": "1000.00", "cuenta_puc": "110505"},
        ]
        state = _state(asientos=asientos)
        report = contador_auditor.run(state)
        assert report.approved is True

    def test_evidence_contains_amounts(self):
        asientos = [
            {"tipo_movimiento": "debito", "valor": "1000.00", "cuenta_puc": "511005"},
            {"tipo_movimiento": "credito", "valor": "800.00", "cuenta_puc": "110505"},
        ]
        state = _state(asientos=asientos)
        report = contador_auditor.run(state)
        finding = next(
            f for f in report.findings if f.rule_id == "CONT-BALANCE-MISMATCH"
        )
        assert "total_debitos" in finding.evidence
        assert "total_creditos" in finding.evidence
        assert "diferencia" in finding.evidence

    def test_invalid_valor_treated_as_zero(self):
        asientos = [
            {
                "tipo_movimiento": "debito",
                "valor": "not-a-number",
                "cuenta_puc": "511005",
            },
            {"tipo_movimiento": "credito", "valor": "0.00", "cuenta_puc": "110505"},
        ]
        state = _state(asientos=asientos)
        report = contador_auditor.run(state)
        assert report.approved is True

    def test_report_duration_and_attempt(self):
        state = _state(asientos=_balanced_asientos())
        report = contador_auditor.run(state, attempt=2)
        assert report.duration_ms >= 0
        assert report.attempt == 2
