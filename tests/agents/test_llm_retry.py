"""Unit tests for app/agents/llm_retry.py."""

from unittest.mock import MagicMock

import pytest

try:
    from langchain_core.exceptions import OutputParserException
except ImportError:  # pragma: no cover
    OutputParserException = None  # type: ignore[assignment,misc]

from app.agents.llm_retry import (
    is_double_entry_violation,
    is_invalid_puc,
    is_parse_error,
    llm_with_parse_retry,
)


@pytest.mark.unit
class TestIsParseError:
    def test_parse_error_true(self):
        if OutputParserException is None:
            pytest.skip("langchain_core not installed")
        assert is_parse_error(OutputParserException("bad json"))

    def test_other_error_false(self):
        assert not is_parse_error(ValueError("nope"))


@pytest.mark.unit
class TestIsDoubleEntryViolation:
    def test_matches_balance_message(self):
        if OutputParserException is None:
            pytest.skip("langchain_core not installed")
        exc = OutputParserException("Double-entry violation: debits (1) != credits (2)")
        assert is_double_entry_violation(exc)

    def test_other_parse_error_false(self):
        if OutputParserException is None:
            pytest.skip("langchain_core not installed")
        assert not is_double_entry_violation(OutputParserException("other"))


@pytest.mark.unit
class TestIsInvalidPuc:
    def test_matches_puc_message(self):
        if OutputParserException is None:
            pytest.skip("langchain_core not installed")
        exc = OutputParserException("Invalid PUC code '4xxx'")
        assert is_invalid_puc(exc)


@pytest.mark.unit
class TestLlmWithParseRetry:
    def test_succeeds_first_try(self):
        method = MagicMock(return_value={"ok": True})
        result = llm_with_parse_retry(method, "raw", agent_label="test")
        assert result == {"ok": True}
        assert method.call_count == 1

    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("transient")
            return {"ok": True}

        result = llm_with_parse_retry(method, "raw", agent_label="test")
        assert result == {"ok": True}
        assert calls["n"] == 3

    def test_parse_error_feeds_correction_feedback(self):
        if OutputParserException is None:
            pytest.skip("langchain_core not installed")

        captured_feedback: list[str | None] = []

        def method(raw_text, correction_feedback=None):
            _ = raw_text
            captured_feedback.append(correction_feedback)
            if len(captured_feedback) < 3:
                raise OutputParserException("Invalid PUC code 'XYZ'")
            return {"ok": True}

        result = llm_with_parse_retry(method, "raw", agent_label="test")
        assert result == {"ok": True}
        assert captured_feedback[0] is None
        assert captured_feedback[1] is not None
        assert "Invalid PUC code" in captured_feedback[1]
        assert captured_feedback[2] is not None

    def test_raises_after_max_retries(self):
        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            raise ConnectionError("network down")

        with pytest.raises(ConnectionError):
            llm_with_parse_retry(method, "raw", max_retries=2, agent_label="test")

    def test_non_transient_exception_propagates_immediately(self):
        calls = {"n": 0}

        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            calls["n"] += 1
            raise RuntimeError("permanent")

        with pytest.raises(RuntimeError):
            llm_with_parse_retry(method, "raw", agent_label="test")
        assert calls["n"] == 1
