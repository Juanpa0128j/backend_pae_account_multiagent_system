"""Unit tests for app/agents/routing/terminal_nodes.py."""

import pytest

from app.agents.routing.terminal_nodes import (
    audit_review_terminal_node,
    error_terminal_node,
    review_terminal_node,
)


def _make_state(**kwargs):
    return {
        "agent_log": [],
        **kwargs,
    }


@pytest.mark.unit
class TestErrorTerminalNode:
    def test_sets_status_error_and_error_message(self):
        state = _make_state(error="Something broke")
        result = error_terminal_node(state)
        assert result is state
        assert result["result"]["status"] == "error"
        assert result["result"]["error"] == "Something broke"
        assert len(result["agent_log"]) == 1
        assert result["agent_log"][0]["event"] == "pipeline_aborted"

    def test_handles_missing_error_defaults_to_unknown(self):
        state = _make_state()
        result = error_terminal_node(state)
        assert result["result"]["error"] == "Unknown error"

    def test_preserves_existing_result(self):
        state = _make_state(error="boom", result={"existing": "data"})
        result = error_terminal_node(state)
        assert result["result"]["existing"] == "data"
        assert result["result"]["status"] == "error"


@pytest.mark.unit
class TestReviewTerminalNode:
    def test_sets_status_pending_review(self):
        state = _make_state()
        result = review_terminal_node(state)
        assert result is state
        assert result["result"]["status"] == "pending_review"
        assert len(result["agent_log"]) == 1
        assert result["agent_log"][0]["event"] == "pipeline_paused"

    def test_preserves_existing_result(self):
        state = _make_state(result={"existing": "data"})
        result = review_terminal_node(state)
        assert result["result"]["existing"] == "data"
        assert result["result"]["status"] == "pending_review"


@pytest.mark.unit
class TestAuditReviewTerminalNode:
    def test_sets_status_pending_audit_review_with_fields(self):
        state = _make_state(
            giveup_record={"attempts": 3},
            audit_rejection_reason="bad math",
        )
        result = audit_review_terminal_node(state)
        assert result is state
        assert result["result"]["status"] == "pending_audit_review"
        assert result["result"]["giveup_record"] == {"attempts": 3}
        assert result["result"]["audit_rejection_reason"] == "bad math"
        assert len(result["agent_log"]) == 1
        assert result["agent_log"][0]["event"] == "pipeline_paused"

    def test_falls_back_to_audit_feedback_when_audit_rejection_reason_missing(self):
        state = _make_state(
            giveup_record=None,
            audit_feedback="fallback reason",
        )
        result = audit_review_terminal_node(state)
        assert result["result"]["audit_rejection_reason"] == "fallback reason"

    def test_handles_missing_reason_fields(self):
        state = _make_state()
        result = audit_review_terminal_node(state)
        assert result["result"]["audit_rejection_reason"] is None

    def test_preserves_existing_result(self):
        state = _make_state(
            result={"existing": "data"},
            audit_rejection_reason="reason",
        )
        result = audit_review_terminal_node(state)
        assert result["result"]["existing"] == "data"
        assert result["result"]["status"] == "pending_audit_review"
