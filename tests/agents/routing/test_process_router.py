"""Unit tests for app.agents.routing.process_router."""

from unittest.mock import MagicMock, patch

import pytest

from app.agents.routing.process_router import route
from app.models.audit import Severity


def _make_state(**kwargs):
    defaults = {
        "agent_log": [],
        "raw_transactions": [],
        "current_agent": "",
        "validation_history": [],
        "result": {},
        "interpreted_data": {},
        "contador_output": {},
        "tributario_output": {},
        "auditor_output": {},
        "audit_reports": [],
        "retry_budget": {},
        "force_persist": False,
        "needs_hitl_review": False,
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.unit
class TestProcessRouter:
    def test_route_no_transactions(self):
        state = _make_state(raw_transactions=[], current_agent="")
        result = route(state)

        assert "no staged transactions" in result["error"]
        assert result["agent_log"][-1]["event"] == "routing_error"
        assert result["agent_log"][-1]["details"]["reason"] == "no_transactions"

    def test_route_start_routes_to_contador(self):
        state = _make_state(raw_transactions=[{"id": "tx-1"}], current_agent="")
        result = route(state)

        assert result["current_agent"] == "contador"
        assert result["current_stage"] == "routing"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["next_agent"] == "contador"
        assert result["agent_log"][-1]["details"]["mode"] == "process"

    @patch("app.agents.supervisor.validate_contador_output_node")
    def test_route_contador_validation_failed(self, mock_validate):
        def _side_effect(s):
            s["correction_feedback"] = "Fix the PUC codes"
            return s

        mock_validate.side_effect = _side_effect

        state = _make_state(current_agent="contador")
        result = route(state)

        assert result["current_agent"] == "contador"
        assert result["correction_feedback"] == "Fix the PUC codes"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["reason"] == "validation_failed"

    @patch("app.agents.supervisor.validate_contador_output_node")
    def test_route_contador_success_to_tributario(self, mock_validate):
        mock_validate.return_value = _make_state(current_agent="contador")

        state = _make_state(current_agent="contador")
        result = route(state)

        assert result["current_agent"] == "tributario"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["next_agent"] == "tributario"

    @patch("app.agents.routing.process_router.get_validator")
    def test_route_tributario_valid(self, mock_get_validator):
        mock_result = MagicMock()
        mock_result.is_valid = True
        mock_result.validated_output = None
        mock_result.error_summary.return_value = ""
        mock_validator = MagicMock()
        mock_validator.validate.return_value = mock_result
        mock_get_validator.return_value = mock_validator

        state = _make_state(
            current_agent="tributario", tributario_output={"impuestos": []}
        )
        result = route(state)

        assert result["current_agent"] == "auditor"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["next_agent"] == "auditor"

    def test_route_auditor_force_persist(self):
        state = _make_state(current_agent="auditor", force_persist=True)
        result = route(state)

        assert result["current_agent"] == "db_persist"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["reason"] == "force_persist"

    @patch("app.agents.supervisor.validate_auditor_output_node")
    def test_route_auditor_approved(self, mock_validate):
        def _side_effect(s):
            s["audit_approved"] = True
            return s

        mock_validate.side_effect = _side_effect

        state = _make_state(current_agent="auditor")
        result = route(state)

        assert result["current_agent"] == "db_persist"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["decision"] == "approved"

    @patch("app.agents.supervisor.validate_auditor_output_node")
    def test_route_auditor_rejected_no_fixable(self, mock_validate):
        def _side_effect(s):
            s["audit_approved"] = False
            return s

        mock_validate.side_effect = _side_effect

        state = _make_state(
            current_agent="auditor",
            audit_reports=[
                {
                    "approved": False,
                    "findings": [
                        {
                            "target": "contador",
                            "rule_id": "PUC-MISMATCH",
                            "severity": Severity.WARNING,
                            "fixable": True,
                            "responsible_agent": "contador",
                            "technical_message": "PUC mismatch",
                            "user_message_es": "Error de PUC",
                        }
                    ],
                }
            ],
            retry_budget={"contador": 2},
            audit_rejection_count=0,
        )
        result = route(state)

        assert result["current_agent"] == "contador"
        assert result["correction_feedback"] == "Audit rejected - please reclassify"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["reason"] == "audit_rejected_llm"
