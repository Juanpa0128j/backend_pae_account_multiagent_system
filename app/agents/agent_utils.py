"""
Shared utilities for agent nodes.
Centralises append_log() so all nodes write to agent_log consistently.
"""

from datetime import datetime, timezone

from app.agents.state import AgentState


def append_log(state: AgentState, agent: str, event: str, details: dict) -> None:
    """
    Append a structured LogEntry to state['agent_log'] in-place.

    Entry schema:
        timestamp : ISO-8601 UTC string
        agent     : name of the producing node/agent
        event     : event type string (routing_start, validation_success, node_start, …)
        details   : free-form dict with event-specific payload

    Safe to call even if agent_log is None or missing from state.
    """
    if state.get("agent_log") is None:
        state["agent_log"] = []
    state["agent_log"].append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "event": event,
            "details": details,
        }
    )
