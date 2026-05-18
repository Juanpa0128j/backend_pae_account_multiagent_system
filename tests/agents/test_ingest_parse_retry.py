"""Tests for LlamaParse transient retry helper."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Stub llm_client and config before importing ingest_agent
_fake_llm = types.ModuleType("app.core.llm_client")


class _DummyLLMClient:
    pass


def _dummy_get_llm_client():
    return MagicMock()


_fake_llm.LLMClient = _DummyLLMClient
_fake_llm.get_llm_client = _dummy_get_llm_client
_fake_llm._compact_error_message = lambda exc, max_len=240: str(exc)[:max_len]
sys.modules.setdefault("app.core.llm_client", _fake_llm)

_fake_config = types.ModuleType("app.core.config")


def _dummy_get_settings():
    return SimpleNamespace(llama_cloud_api_key="test-key")


_fake_config.get_settings = _dummy_get_settings
_fake_config.settings = SimpleNamespace(
    llama_cloud_api_key="test-key",
    database_url="postgresql://localhost/test",
    app_env="test",
)
sys.modules.setdefault("app.core.config", _fake_config)

from app.agents import ingest_agent  # noqa: E402


def test_transient_classifier_recognises_httpx_timeout() -> None:
    assert (
        ingest_agent._is_transient_parse_error(httpx.TimeoutException("slow")) is True
    )


def test_transient_classifier_recognises_httpx_network_error() -> None:
    assert (
        ingest_agent._is_transient_parse_error(httpx.NetworkError("network failed"))
        is True
    )


def test_transient_classifier_recognises_httpx_remote_protocol_error() -> None:
    assert (
        ingest_agent._is_transient_parse_error(
            httpx.RemoteProtocolError("protocol error")
        )
        is True
    )


def test_transient_classifier_recognises_503_http_status_error() -> None:
    """Recognize 5xx HTTP status errors as transient."""
    response = MagicMock()
    response.status_code = 503
    exc = httpx.HTTPStatusError("Service Unavailable", request=None, response=response)
    assert ingest_agent._is_transient_parse_error(exc) is True


def test_transient_classifier_recognises_502_http_status_error() -> None:
    """Recognize 502 Bad Gateway as transient."""
    response = MagicMock()
    response.status_code = 502
    exc = httpx.HTTPStatusError("Bad Gateway", request=None, response=response)
    assert ingest_agent._is_transient_parse_error(exc) is True


def test_transient_classifier_rejects_404_http_status_error() -> None:
    """Reject 4xx errors as non-transient."""
    response = MagicMock()
    response.status_code = 404
    exc = httpx.HTTPStatusError("Not Found", request=None, response=response)
    assert ingest_agent._is_transient_parse_error(exc) is False


def test_transient_classifier_recognises_503_runtime_error() -> None:
    assert (
        ingest_agent._is_transient_parse_error(
            RuntimeError("LlamaParse returned 503 service unavailable")
        )
        is True
    )


def test_transient_classifier_recognises_timeout_runtime_error() -> None:
    assert (
        ingest_agent._is_transient_parse_error(
            RuntimeError("Request timeout after 30s")
        )
        is True
    )


def test_transient_classifier_recognises_connection_runtime_error() -> None:
    assert (
        ingest_agent._is_transient_parse_error(RuntimeError("Connection refused"))
        is True
    )


def test_transient_classifier_rejects_value_error() -> None:
    assert ingest_agent._is_transient_parse_error(ValueError("bad schema")) is False


def test_transient_classifier_rejects_key_error() -> None:
    assert ingest_agent._is_transient_parse_error(KeyError("missing")) is False


def test_retry_succeeds_after_transient_failures() -> None:
    # Arrange — fail twice with timeout, succeed third
    parser = MagicMock()
    attempts = {"n": 0}

    def _flaky(_path):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.TimeoutException("transient")
        return [MagicMock(text="parsed content")]

    parser.load_data.side_effect = _flaky

    # Act
    with patch("tenacity.nap.time.sleep", lambda _: None):
        result = ingest_agent._llama_parse_with_retry(parser, "/tmp/x.pdf")

    # Assert
    assert attempts["n"] == 3
    assert result[0].text == "parsed content"


def test_retry_gives_up_after_three_transient_failures() -> None:
    # Arrange
    parser = MagicMock()
    parser.load_data.side_effect = httpx.TimeoutException("always-fails")

    # Act / Assert
    with patch("tenacity.nap.time.sleep", lambda _: None):
        with pytest.raises(httpx.TimeoutException):
            ingest_agent._llama_parse_with_retry(parser, "/tmp/x.pdf")

    assert parser.load_data.call_count == 3


def test_retry_does_not_retry_permanent_error() -> None:
    # Arrange — schema/value error, non-retryable
    parser = MagicMock()
    parser.load_data.side_effect = ValueError("bad doc")

    # Act / Assert
    with pytest.raises(ValueError):
        ingest_agent._llama_parse_with_retry(parser, "/tmp/x.pdf")

    assert parser.load_data.call_count == 1  # no retry


def test_retry_does_not_retry_runtime_error_without_transient_hint() -> None:
    # Arrange — RuntimeError without transient keywords
    parser = MagicMock()
    parser.load_data.side_effect = RuntimeError("unrelated error")

    # Act / Assert
    with pytest.raises(RuntimeError):
        ingest_agent._llama_parse_with_retry(parser, "/tmp/x.pdf")

    assert parser.load_data.call_count == 1


def test_retry_succeeds_after_504_error() -> None:
    # Arrange — fail once with 504, then succeed
    parser = MagicMock()
    attempts = {"n": 0}

    def _flaky(_path):
        attempts["n"] += 1
        if attempts["n"] == 1:
            response = MagicMock()
            response.status_code = 504
            raise httpx.HTTPStatusError(
                "Gateway Timeout", request=None, response=response
            )
        return [MagicMock(text="success after 504")]

    parser.load_data.side_effect = _flaky

    # Act
    with patch("tenacity.nap.time.sleep", lambda _: None):
        result = ingest_agent._llama_parse_with_retry(parser, "/tmp/x.pdf")

    # Assert
    assert attempts["n"] == 2
    assert result[0].text == "success after 504"
