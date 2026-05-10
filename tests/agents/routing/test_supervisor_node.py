"""Tests for supervisor_node thin dispatcher."""

from unittest.mock import patch

from app.agents.routing import supervisor_node
from app.agents.state import AgentState


class TestSupervisorNode:
    """Test suite for supervisor_node dispatcher."""

    def test_supervisor_init_defaults(self) -> None:
        """Empty state gets defaults initialized."""
        state: AgentState = {}

        with patch.object(
            supervisor_node.ingest_router, "route", return_value=state
        ) as mock_ingest:
            result = supervisor_node.supervisor_node(state)

        assert result["validation_history"] == []
        assert result["current_agent"] == ""
        assert result["retry_count"] == 0
        assert result["correction_feedback"] is None
        assert isinstance(result["agent_log"], list)
        assert result["audit_decision"] is None
        assert result["audit_feedback"] is None
        assert result["audit_rejection_count"] == 0
        mock_ingest.assert_called_once()

    def test_supervisor_ingest_mode(self) -> None:
        """mode=ingest with no current_agent delegates to ingest_router."""
        state: AgentState = {"mode": "ingest"}
        expected: AgentState = {"mode": "ingest", "current_agent": "ingesta"}

        with patch.object(
            supervisor_node.ingest_router, "route", return_value=expected
        ) as mock_ingest:
            result = supervisor_node.supervisor_node(state)

        mock_ingest.assert_called_once_with(state)
        assert result == expected

    def test_supervisor_process_mode(self) -> None:
        """mode=process delegates to process_router."""
        state: AgentState = {"mode": "process", "current_agent": ""}
        expected: AgentState = {"mode": "process", "current_agent": "contador"}

        with patch.object(
            supervisor_node.process_router, "route", return_value=expected
        ) as mock_process:
            result = supervisor_node.supervisor_node(state)

        mock_process.assert_called_once_with(state)
        assert result == expected

    def test_supervisor_reporting_mode(self) -> None:
        """mode=reporting sets current_agent to reportero."""
        state: AgentState = {"mode": "reporting"}
        result = supervisor_node.supervisor_node(state)

        assert result["current_agent"] == "reportero"
        assert any(
            entry.get("event") == "routing_complete"
            for entry in result.get("agent_log", [])
        )

    def test_supervisor_unknown_mode(self) -> None:
        """Unknown mode sets an error and returns state."""
        state: AgentState = {"mode": "unknown_mode", "current_agent": "some_agent"}
        result = supervisor_node.supervisor_node(state)

        assert "unknown mode" in result.get("error", "")
        assert any(
            entry.get("event") == "routing_error"
            for entry in result.get("agent_log", [])
        )


class TestRouteAfterSupervisor:
    """Test suite for route_after_supervisor conditional edge."""

    def test_route_after_supervisor_error(self) -> None:
        """When error is set, route to error_terminal."""
        state: AgentState = {"error": "something went wrong"}
        assert supervisor_node.route_after_supervisor(state) == "error_terminal"

    def test_route_after_supervisor_audit_review(self) -> None:
        """audit_review_terminal takes priority."""
        state: AgentState = {"current_agent": "audit_review_terminal"}
        assert supervisor_node.route_after_supervisor(state) == "audit_review_terminal"

    def test_route_after_supervisor_ingesta(self) -> None:
        """current_agent=ingesta routes to ingesta node."""
        state: AgentState = {"current_agent": "ingesta"}
        assert supervisor_node.route_after_supervisor(state) == "ingesta"
