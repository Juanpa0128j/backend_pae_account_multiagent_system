"""Tests SINGLE_PASS_DOC_TYPES bypass in supervisor audit routing."""

from unittest.mock import patch

from app.agents.supervisor import supervisor_node


def _state_after_first_audit(doc_type: str, findings: list[dict]) -> dict:
    return {
        "current_agent": "auditor",
        "audit_approved": False,
        "audit_reports": [{"approved": False, "findings": findings}],
        "document_classification": {"doc_type": doc_type},
        "agent_log": [],
        "validation_history": [],
        "retry_budget": {},
        "mode": "process",
    }


class TestSinglePassBypass:
    def test_extracto_bancario_bypasses_audit_loop_to_persist(self):
        findings = [
            {
                "target": "contador",
                "rule_id": "doble_entry_minor",
                "severity": "error",
                "fixable": True,
                "responsible_agent": "contador",
                "technical_message": "minor",
                "user_message_es": "menor",
            }
        ]
        state = _state_after_first_audit("extracto_bancario", findings)

        with patch(
            "app.agents.routing.process_router.validate_auditor_output_node",
            side_effect=lambda s: s,
        ):
            result = supervisor_node(state)

        assert result["current_agent"] == "db_persist"
        assert result.get("has_warnings") is True

    def test_factura_venta_does_not_bypass_loop(self):
        findings = [
            {
                "target": "contador",
                "rule_id": "doble_entry_minor",
                "severity": "error",
                "fixable": True,
                "responsible_agent": "contador",
                "technical_message": "minor",
                "user_message_es": "menor",
            }
        ]
        state = _state_after_first_audit("factura_venta", findings)

        with patch(
            "app.agents.routing.process_router.validate_auditor_output_node",
            side_effect=lambda s: s,
        ):
            result = supervisor_node(state)

        assert result["current_agent"] != "db_persist"

    def test_extracto_with_blocker_still_goes_to_hitl(self):
        findings = [
            {
                "target": "contador",
                "rule_id": "puc_missing",
                "severity": "blocker",
                "fixable": False,
                "responsible_agent": "contador",
                "technical_message": "blocker",
                "user_message_es": "bloqueante",
            }
        ]
        state = _state_after_first_audit("extracto_bancario", findings)

        with patch(
            "app.agents.routing.process_router.validate_auditor_output_node",
            side_effect=lambda s: s,
        ):
            result = supervisor_node(state)

        assert result["current_agent"] == "audit_review_terminal"
