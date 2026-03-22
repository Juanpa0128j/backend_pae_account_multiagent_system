"""
Shared test fixtures for the PAE multi-agent system tests.

Centralises base state builders so every test file uses the same canonical
state shape, preventing divergence that caused PR #16 review findings.
"""

import pytest


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
