"""Unit tests for supervisor_node_dispatcher."""

from unittest.mock import patch


def _state(mode="process", **kwargs):
    base = {
        "mode": mode,
        "current_agent": "",
        "raw_transactions": [{"id": 1}],
        "validation_history": [],
        "retry_count": None,
        "correction_feedback": None,
        "agent_log": None,
    }
    return {**base, **kwargs}


def test_process_mode_routes_to_contador():
    from app.agents.routing.supervisor_node import supervisor_node_dispatcher

    result = supervisor_node_dispatcher(_state(mode="process"))
    assert result["current_agent"] == "contador"


def test_ingest_mode_calls_route_ingest():
    from app.agents.routing.supervisor_node import supervisor_node_dispatcher

    with patch("app.agents.routing.supervisor_node.route_ingest") as mock_ri:
        mock_ri.return_value = {"current_agent": "ingest_agent"}
        result = supervisor_node_dispatcher(_state(mode="ingest"))
    mock_ri.assert_called_once()
    assert result["current_agent"] == "ingest_agent"


def test_unknown_mode_calls_route_ingest():
    from app.agents.routing.supervisor_node import supervisor_node_dispatcher

    with patch("app.agents.routing.supervisor_node.route_ingest") as mock_ri:
        mock_ri.return_value = {"current_agent": "ingesta", "mode": ""}
        supervisor_node_dispatcher(_state(mode=""))
    mock_ri.assert_called_once()
