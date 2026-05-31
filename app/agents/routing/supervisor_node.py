"""
Supervisor node dispatcher.

Thin dispatcher: routes to ingest or process pipeline based on state["mode"].
"""

from app.agents.routing.ingest_router import route_ingest
from app.agents.routing.process_router import process_supervisor_node
from app.agents.state import AgentState


def supervisor_node_dispatcher(state: AgentState) -> AgentState:
    """Thin dispatcher: routes to ingest or process pipeline based on mode."""
    if state.get("mode") == "process":
        return process_supervisor_node(state)
    return route_ingest(state)
