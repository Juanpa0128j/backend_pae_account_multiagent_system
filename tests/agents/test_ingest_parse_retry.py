"""Tests for the v2 LlamaCloud parse options builder, extractor, and retry helper."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

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
from app.agents.ingest_agent import (  # noqa: E402
    _MODE_TO_TIER,
    _build_parse_options,
    _extract_text,
    _is_transient_parse_error,
    _parse_with_retry,
)


class TestModeToTier:
    def test_canonical_mapping(self):
        assert _MODE_TO_TIER == {
            "fast": "fast",
            "standard": "cost_effective",
            "agentic": "agentic",
            "agentic_plus": "agentic_plus",
        }

    def test_unknown_mode_falls_through_with_warning(self, caplog):
        # ingest_agent's logger disables propagation (avoids duplicate root
        # handler lines), so attach caplog's handler directly to capture it.
        ingest_agent.logger.addHandler(caplog.handler)
        try:
            with caplog.at_level("WARNING", logger="app.agents.ingest"):
                opts = _build_parse_options("premium")  # legacy string, post-migration
        finally:
            ingest_agent.logger.removeHandler(caplog.handler)
        assert opts["tier"] == "cost_effective"
        assert any("unknown parser mode" in r.message.lower() for r in caplog.records)

    def test_options_shape(self):
        opts = _build_parse_options("standard")
        assert opts["tier"] == "cost_effective"
        assert opts["version"]  # pinned, non-empty
        assert "markdown_full" in opts["expand"] and "text_full" in opts["expand"]


class TestExtractText:
    def test_prefers_markdown(self):
        class R:
            markdown = "# md"
            text = "plain"

        assert _extract_text(R()) == "# md"

    def test_falls_back_to_text_when_markdown_empty(self):
        class R:
            markdown = "   "
            text = "plain"

        assert _extract_text(R()) == "plain"

    def test_empty_both_returns_empty(self):
        class R:
            markdown = ""
            text = None

        assert _extract_text(R()) == ""


class TestRetry:
    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectTimeout("boom")
            return "ok"

        assert _parse_with_retry(flaky) == "ok"
        assert calls["n"] == 3

    def test_no_retry_on_4xx(self):
        resp = httpx.Response(422, request=httpx.Request("POST", "http://x"))

        def bad():
            raise httpx.HTTPStatusError("422", request=resp.request, response=resp)

        with pytest.raises(httpx.HTTPStatusError):
            _parse_with_retry(bad)

    def test_keyerror_is_not_transient(self):
        assert _is_transient_parse_error(KeyError("markdown")) is False
