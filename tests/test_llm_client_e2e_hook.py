import pytest

from app.core.llm_client import E2ELLMFailure, get_llm_client


def test_returns_real_client_when_flag_unset(monkeypatch):
    monkeypatch.delenv("E2E_FORCE_LLM_FAIL", raising=False)
    get_llm_client.cache_clear()
    client = get_llm_client()
    assert client is not None


def test_raises_when_flag_set(monkeypatch):
    monkeypatch.setenv("E2E_FORCE_LLM_FAIL", "1")
    get_llm_client.cache_clear()
    with pytest.raises(E2ELLMFailure):
        get_llm_client()
    get_llm_client.cache_clear()
