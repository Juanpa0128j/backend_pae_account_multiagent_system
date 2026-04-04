from pydantic import BaseModel

from app.core.llm_client import LLMClient, _compact_error_message


class _DemoSchema(BaseModel):
    ok: str


class _FailingProvider:
    def __init__(self, error: Exception):
        self._error = error

    def invoke(self, schema_cls, prompt):
        raise self._error


class _OkProvider:
    def invoke(self, schema_cls, prompt):
        return schema_cls(ok="ok")


def _build_client(*, openai=None, gemini_key="", gemini=None, groq_key="", groq=None):
    client = LLMClient.__new__(LLMClient)
    client._openai = openai
    client._openai_key = "set" if openai is not None else ""
    client._gemini_key = gemini_key
    client._groq_key = groq_key
    client._gemini = gemini
    client._groq = groq

    if gemini is None:
        client._get_gemini = lambda: None
    else:
        client._get_gemini = lambda: gemini

    if groq is None:
        client._get_groq = lambda: None
    else:
        client._get_groq = lambda: groq

    return client


def test_compact_error_message_single_line_and_trimmed():
    err = RuntimeError("line1\nline2\nline3")
    compact = _compact_error_message(err, max_len=10)
    assert "\n" not in compact
    assert compact.endswith("...")


def test_invoke_fails_fast_on_first_permanent_error():
    """Fails immediately on first provider's permanent error; doesn't try subsequent providers."""
    client = _build_client(
        openai=_FailingProvider(RuntimeError("invalid API key")),
        gemini_key="x",
        gemini=_OkProvider(),  # This would succeed, but won't be tried
    )

    try:
        client._invoke(_DemoSchema, "ping")
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        # Error message is from OpenAI (first provider), not the comprehensive trace
        msg = str(exc)
        assert "invalid API key" in msg
        # Should NOT show the "All configured LLM providers failed" message
        assert "All configured LLM providers failed" not in msg


def test_invoke_reports_comprehensive_trace_on_all_quota_exhaustion():
    """When ALL providers are quota-exhausted, reports comprehensive trace of all attempts."""
    client = _build_client(
        openai=_FailingProvider(RuntimeError("429 Quota exceeded")),
        gemini_key="x",
        gemini=_FailingProvider(RuntimeError("RESOURCE_EXHAUSTED: quota")),
    )

    try:
        client._invoke(_DemoSchema, "ping")
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        msg = str(exc)
        assert "All configured LLM providers failed for _DemoSchema" in msg
        assert "OpenAI: RuntimeError: 429 Quota exceeded" in msg
        assert "Gemini: RuntimeError: RESOURCE_EXHAUSTED: quota" in msg


def test_invoke_falls_back_on_quota_error_only():
    """Falls back to next provider only on quota errors, not on permanent failures."""
    client = _build_client(
        openai=_FailingProvider(RuntimeError("429 Rate limit exceeded")),
        gemini_key="x",
        gemini=_OkProvider(),
    )

    out = client._invoke(_DemoSchema, "ping")
    assert out.ok == "ok"


def test_invoke_fails_fast_on_permanent_error():
    """Fails immediately on non-quota errors (auth, schema, etc) per CLAUDE.md: fail fast."""
    client = _build_client(
        openai=_FailingProvider(RuntimeError("invalid API key")),
        gemini_key="x",
        gemini=_OkProvider(),
    )

    try:
        client._invoke(_DemoSchema, "ping")
        assert False, "Expected RuntimeError to be raised on permanent error"
    except RuntimeError as exc:
        assert "invalid API key" in str(exc)
