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


def test_invoke_reports_provider_attempt_trace_on_total_failure():
    client = _build_client(
        openai=_FailingProvider(RuntimeError("openai timed out")),
        gemini_key="x",
        gemini=_FailingProvider(RuntimeError("gemini 429 quota")),
    )

    try:
        client._invoke(_DemoSchema, "ping")
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        msg = str(exc)
        assert "All configured LLM providers failed for _DemoSchema" in msg
        assert "OpenAI: RuntimeError: openai timed out" in msg
        assert "Gemini: RuntimeError: gemini 429 quota" in msg


def test_invoke_falls_back_and_returns_next_provider_success():
    client = _build_client(
        openai=_FailingProvider(RuntimeError("first provider down")),
        gemini_key="x",
        gemini=_OkProvider(),
    )

    out = client._invoke(_DemoSchema, "ping")
    assert out.ok == "ok"
