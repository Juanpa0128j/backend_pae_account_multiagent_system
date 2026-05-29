from app.agents.routing.terminal_nodes import (
    audit_review_terminal_node,
    error_terminal_node,
    review_terminal_node,
)


def _state(**kwargs):
    base = {
        "error": None,
        "current_agent": "",
        "agent_log": [],
        "giveup_record": None,
        "current_stage": None,
        "validation_history": [],
    }
    return {**base, **kwargs}


def test_error_terminal_sets_agent():
    result = error_terminal_node(_state(error="boom"))
    assert result["current_agent"] == "error_terminal"


def test_review_terminal_sets_agent():
    result = review_terminal_node(_state())
    assert result["current_agent"] == "review_terminal"


def test_audit_review_terminal_sets_agent():
    result = audit_review_terminal_node(_state())
    assert result["current_agent"] == "audit_review_terminal"


def test_error_terminal_preserves_error():
    result = error_terminal_node(_state(error="something bad"))
    assert result["error"] == "something bad"
