"""Unit tests for app/agents/auditors/pre_persist_auditor.py"""

import pytest

from app.agents.auditors import pre_persist_auditor
from app.models.audit import AuditTarget, Severity


def _state(asientos=None, pending_transaction_id="pending_001"):
    return {
        "mode": "process",
        "contador_output": {"asientos": asientos} if asientos is not None else {},
        "pending_transaction_id": pending_transaction_id,
    }


@pytest.mark.unit
class TestPrePersistAuditor:
    def test_process_balanced_asientos_is_approved(self):
        state = _state(
            asientos=[
                {"tipo_movimiento": "debito", "valor": "1000", "cuenta_puc": "511005"},
                {"tipo_movimiento": "credito", "valor": "1000", "cuenta_puc": "220505"},
            ]
        )

        report = pre_persist_auditor.run(state)
        assert report.target == AuditTarget.PRE_PERSIST
        assert report.approved is True
        assert report.findings == []

    def test_missing_asientos_emits_blocker(self):
        state = _state(asientos=[])
        report = pre_persist_auditor.run(state)

        assert report.approved is False
        finding = next(f for f in report.findings if f.rule_id == "PREP-NO-ASIENTOS")
        assert finding.severity == Severity.BLOCKER
        assert finding.fixable is False

    def test_unbalanced_asientos_emits_blocker(self):
        state = _state(
            asientos=[
                {"tipo_movimiento": "debito", "valor": "1000", "cuenta_puc": "511005"},
                {"tipo_movimiento": "credito", "valor": "900", "cuenta_puc": "220505"},
            ]
        )
        report = pre_persist_auditor.run(state)

        assert report.approved is False
        finding = next(
            f for f in report.findings if f.rule_id == "PREP-PARTIDA-DOBLE-MISMATCH"
        )
        assert finding.severity == Severity.BLOCKER
        assert finding.fixable is False

    def test_missing_pending_id_emits_blocker(self):
        state = _state(
            asientos=[
                {"tipo_movimiento": "debito", "valor": "1000", "cuenta_puc": "511005"},
                {"tipo_movimiento": "credito", "valor": "1000", "cuenta_puc": "220505"},
            ],
            pending_transaction_id="",
        )

        report = pre_persist_auditor.run(state)
        finding = next(
            f
            for f in report.findings
            if f.rule_id == "PREP-MISSING-PENDING-TRANSACTION"
        )
        assert finding.severity == Severity.BLOCKER
        assert finding.fixable is False
