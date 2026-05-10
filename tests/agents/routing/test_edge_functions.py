"""Unit tests for app.agents.routing.edge_functions."""

import pytest

from app.agents.routing.edge_functions import (
    should_retry_agent,
    should_retry_auditor,
    should_retry_contador,
)
from app.agents.validation_rules import MAX_AUDITOR_RETRIES, MAX_CONTADOR_RETRIES


def _make_state(**kwargs):
    return {"retry_count": 0, **kwargs}


@pytest.mark.unit
class TestShouldRetryAgent:
    def test_error_returns_error(self):
        state = _make_state(error="boom")
        assert should_retry_agent(state) == "error"

    def test_correction_feedback_returns_retry(self):
        state = _make_state(correction_feedback="fix this")
        assert should_retry_agent(state) == "retry"

    def test_no_error_no_feedback_returns_end(self):
        state = _make_state()
        assert should_retry_agent(state) == "end"

    def test_error_takes_precedence_over_feedback(self):
        state = _make_state(error="boom", correction_feedback="fix this")
        assert should_retry_agent(state) == "error"


@pytest.mark.unit
class TestShouldRetryContador:
    def test_feedback_and_under_max_returns_retry(self):
        state = _make_state(
            correction_feedback="fix this",
            retry_count=MAX_CONTADOR_RETRIES - 1,
        )
        assert should_retry_contador(state) == "retry"

    def test_at_max_returns_end(self):
        state = _make_state(
            correction_feedback="fix this",
            retry_count=MAX_CONTADOR_RETRIES,
        )
        assert should_retry_contador(state) == "end"

    def test_no_feedback_returns_end(self):
        state = _make_state(retry_count=0)
        assert should_retry_contador(state) == "end"

    def test_over_max_returns_end(self):
        state = _make_state(
            correction_feedback="fix this",
            retry_count=MAX_CONTADOR_RETRIES + 1,
        )
        assert should_retry_contador(state) == "end"


@pytest.mark.unit
class TestShouldRetryAuditor:
    def test_feedback_and_under_max_returns_retry(self):
        state = _make_state(
            correction_feedback="fix this",
            retry_count=MAX_AUDITOR_RETRIES - 1,
        )
        assert should_retry_auditor(state) == "retry"

    def test_at_max_returns_end(self):
        state = _make_state(
            correction_feedback="fix this",
            retry_count=MAX_AUDITOR_RETRIES,
        )
        assert should_retry_auditor(state) == "end"

    def test_no_feedback_returns_end(self):
        state = _make_state(retry_count=0)
        assert should_retry_auditor(state) == "end"

    def test_over_max_returns_end(self):
        state = _make_state(
            correction_feedback="fix this",
            retry_count=MAX_AUDITOR_RETRIES + 1,
        )
        assert should_retry_auditor(state) == "end"
