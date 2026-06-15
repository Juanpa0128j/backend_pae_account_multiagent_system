"""
Shared test fixtures for the PAE multi-agent system tests.

Centralises base state builders so every test file uses the same canonical
state shape, preventing divergence that caused PR #16 review findings.
"""

from uuid import UUID

import pytest

from app.core.auth import CurrentUser, get_current_user
from main import app


@pytest.fixture(autouse=True)
def override_auth():
    """Override get_current_user for all tests so endpoints don't reject unauthenticated requests."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=UUID("00000000-0000-0000-0000-000000000000"),
        email="test@test.com",
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def disable_rate_limits():
    """Disable slowapi rate limiting for all tests so direct function calls don't fail."""
    from app.core.limiter import limiter

    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


@pytest.fixture(autouse=True)
def reset_compiled_graph_cache():
    """Clear the process-wide compiled-graph singleton between tests.

    get_compiled_agent_graph() is @lru_cache'd in production (intended
    singleton), but that cache leaks across tests: once one test populates it
    with the real graph, later tests that monkeypatch create_agent_graph get
    the stale cached graph instead of their patched one. Clearing before each
    test restores isolation without weakening the prod cache.
    """
    from app.agents.graph import get_compiled_agent_graph

    get_compiled_agent_graph.cache_clear()
    yield
    get_compiled_agent_graph.cache_clear()


def base_state(**overrides) -> dict:
    """
    Return a fully-populated AgentState dict with safe default values.
    All fields match the current AgentState TypedDict definition.
    Use keyword overrides to set only the fields relevant to your test.

    Example:
        state = base_state(report_type="balance", mode="reporting")
    """
    state = {
        "file_path": "",
        "file_paths": [],
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
        "mode": "ingest",
        "raw_transactions": [],
        "contador_output": {},
        "tributario_output": {},
        "company_config": None,
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": None,
        "agent_log": [],
        "auditor_output": {},
        "audit_approved": None,
        "audit_rejection_reason": None,
        "audit_decision": None,
        "audit_feedback": None,
        "audit_rejection_count": 0,
        "report_type": None,
        "report_params": None,
        "document_classification": None,
        "pathway": None,
        "parser_mode": None,
        "parsed_content": None,
        "company_nit": None,
    }
    state.update(overrides)
    return state


def base_reporting_state(report_type: str, **params) -> dict:
    """
    Convenience builder for reporting pipeline states.

    Args:
        report_type: "balance" | "pnl" | "cashflow" | "iva" | "withholdings"
        **params: Optional report_params fields, e.g. start_date="2026-01-01"
    """
    return base_state(
        mode="reporting",
        report_type=report_type,
        report_params=params or {},
    )


# ---------------------------------------------------------------------------
# Pytest fixtures (available to all test files without explicit import)
# ---------------------------------------------------------------------------


@pytest.fixture
def reporting_state():
    """Returns the base_reporting_state builder so tests can call it inline."""
    return base_reporting_state


@pytest.fixture
def full_base_state():
    """Returns the base_state builder so tests can call it inline."""
    return base_state
