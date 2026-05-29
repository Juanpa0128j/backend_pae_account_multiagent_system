"""Unit tests for process_router.process_supervisor_node."""

from app.agents.routing.process_router import process_supervisor_node


def _state(**kwargs):
    base = {
        "raw_transactions": [{"id": 1}],
        "validation_history": [],
        "retry_count": None,
        "correction_feedback": None,
        "agent_log": None,
        "mode": None,
    }
    return {**base, **kwargs}


def test_routes_to_contador():
    result = process_supervisor_node(_state())
    assert result["current_agent"] == "contador"


def test_error_when_no_transactions():
    result = process_supervisor_node(_state(raw_transactions=[]))
    assert result.get("error") is not None


def test_sets_mode_process():
    result = process_supervisor_node(_state())
    assert result["mode"] == "process"


def test_sets_current_stage_routing():
    result = process_supervisor_node(_state())
    assert result["current_stage"] == "routing"


def test_initialises_validation_history():
    result = process_supervisor_node(_state(validation_history=None))
    assert result["validation_history"] == []


def test_initialises_agent_log():
    result = process_supervisor_node(_state(agent_log=None))
    assert isinstance(result["agent_log"], list)
