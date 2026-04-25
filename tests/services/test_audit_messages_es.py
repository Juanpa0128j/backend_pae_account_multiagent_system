"""Unit tests for app/services/audit_messages_es.py."""

import pytest
from unittest.mock import patch

from app.services.audit_messages_es import (
    MESSAGES,
    get_agent_summary_es,
    get_message,
)


@pytest.mark.unit
class TestGetMessage:
    def test_known_rule_returns_message(self):
        msg, action = get_message("CONT-RAG-MISS")
        assert "PUC" in msg
        assert action is not None

    def test_unknown_rule_returns_generic_never_raises(self):
        msg, action = get_message("RULE-DOES-NOT-EXIST-XYZ")
        assert isinstance(msg, str)
        assert len(msg) > 0
        assert action is not None

    def test_evidence_substitution(self):
        msg, action = get_message(
            "TRIB-RETENCION-MISMATCH",
            evidence={
                "declared_rate": "3.5",
                "expected_rate": "4.0",
                "concept_name": "Honorarios",
            },
        )
        assert "3.5" in msg
        assert "4.0" in msg
        assert "Honorarios" in msg

    def test_partial_evidence_does_not_raise(self):
        msg, action = get_message("TRIB-RETENCION-MISMATCH", evidence={})
        assert isinstance(msg, str)

    def test_partial_evidence_logs_warning(self):
        with patch("app.services.audit_messages_es.logger.warning") as mock_warning:
            msg, action = get_message(
                "ING-DUPLICATE-DETECTED",
                evidence={"nit": "900123456"},
            )

        assert isinstance(msg, str)
        assert mock_warning.called

    def test_all_registered_rule_ids_have_user_message(self):
        for rule_id, entry in MESSAGES.items():
            assert "user_message_es" in entry, f"{rule_id} missing user_message_es"
            assert entry["user_message_es"], f"{rule_id} has empty user_message_es"

    def test_pers_statement_derivation_fail(self):
        msg, action = get_message("PERS-STATEMENT-DERIVATION-FAIL")
        assert "estados financieros" in msg
        assert action is not None

    def test_pers_via_b_partial(self):
        msg, _ = get_message("PERS-VIA-B-PARTIAL")
        assert "Vía B" in msg

    def test_ing_extraction_partial(self):
        msg, _ = get_message("ING-EXTRACTION-PARTIAL")
        assert "parcial" in msg.lower()

    def test_none_evidence_ok(self):
        msg, action = get_message("CONT-RAG-MISS", evidence=None)
        assert isinstance(msg, str)


@pytest.mark.unit
class TestGetAgentSummaryEs:
    def test_known_agent_ok(self):
        summary = get_agent_summary_es("contador")
        assert "contador" in summary.lower()

    def test_known_agent_failed(self):
        summary_ok = get_agent_summary_es("contador", failed=False)
        summary_fail = get_agent_summary_es("contador", failed=True)
        assert isinstance(summary_fail, str)
        assert summary_fail != summary_ok

    def test_unknown_agent_returns_string(self):
        summary = get_agent_summary_es("unknown_agent_xyz")
        assert "unknown_agent_xyz" in summary

    def test_all_known_agents_have_summary(self):
        known = [
            "supervisor",
            "ingesta",
            "ingest",
            "contador",
            "tributario",
            "auditor",
            "persist",
            "db_persist",
            "reportero",
        ]
        for agent in known:
            summary = get_agent_summary_es(agent)
            assert isinstance(summary, str) and len(summary) > 5
