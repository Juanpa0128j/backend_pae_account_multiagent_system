"""Conditional edge functions for the unified agent graph."""

from app.agents.state import AgentState
from app.agents.validation_rules import MAX_AUDITOR_RETRIES, MAX_CONTADOR_RETRIES


def should_retry_agent(state: AgentState) -> str:
    """Conditional edge for ingest graph: retry, error bypass, or proceed."""
    if state.get("error"):
        return "error"
    if state.get("correction_feedback"):
        return "retry"
    return "end"


def should_retry_contador(state: AgentState) -> str:
    """Conditional edge for contador retries in the process graph."""
    if (
        state.get("correction_feedback")
        and state.get("retry_count", 0) < MAX_CONTADOR_RETRIES
    ):
        return "retry"
    return "end"


def should_retry_auditor(state: AgentState) -> str:
    """Conditional edge for auditor retries in the process graph."""
    if (
        state.get("correction_feedback")
        and state.get("retry_count", 0) < MAX_AUDITOR_RETRIES
    ):
        return "retry"
    return "end"
