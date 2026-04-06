"""
Unit tests for ingest_node and retry helper routes.

Scope:
- input format routing: xlsx, xml, pdf/image, unsupported
- parse cache: hit/miss and markdown->text fallback
- dispatch by doc_type and pathway hints
- retry flow with correction feedback reuse
- error branches: empty text, missing extraction method, invalid output structure
- transient retry helper behavior
"""

from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Stub llm_client before importing ingest_agent so tests can run without
# optional langchain dependencies in lightweight environments.
_fake_llm = types.ModuleType("app.core.llm_client")


class _DummyLLMClient:
    pass


def _dummy_get_llm_client():
    return MagicMock()


_fake_llm.LLMClient = _DummyLLMClient
_fake_llm.get_llm_client = _dummy_get_llm_client
# Provide a no-op stub so other test modules that import _compact_error_message
# don't fail when the stub is already cached in sys.modules.
_fake_llm._compact_error_message = lambda exc, max_len=240: str(exc)[:max_len]
sys.modules.setdefault("app.core.llm_client", _fake_llm)

_fake_config = types.ModuleType("app.core.config")


def _dummy_get_settings():
    return SimpleNamespace(llama_cloud_api_key="test-key")


_fake_config.get_settings = _dummy_get_settings
# Provide settings object so database.py (imported transitively by other test
# modules) can do `from app.core.config import settings` without failing.
_fake_config.settings = SimpleNamespace(
    llama_cloud_api_key="test-key",
    database_url="postgresql://localhost/test",
    app_env="test",
)
sys.modules.setdefault("app.core.config", _fake_config)

from app.agents import ingest_agent  # noqa: E402
from tests.conftest import base_state  # noqa: E402


@pytest.fixture
def pdf_file(tmp_path: Path) -> str:
    p = tmp_path / "invoice test.pdf"
    p.write_bytes(b"%PDF-1.4 dummy")
    return str(p)


@pytest.fixture
def xlsx_file(tmp_path: Path) -> str:
    p = tmp_path / "sheet.xlsx"
    p.write_bytes(b"dummy")
    return str(p)


@pytest.fixture
def xml_file(tmp_path: Path) -> str:
    p = tmp_path / "doc.xml"
    p.write_text("<xml/>", encoding="utf-8")
    return str(p)


def _build_client(method_name: str, return_value):
    client = MagicMock()
    setattr(client, method_name, MagicMock(return_value=return_value))
    return client


class TestGeminiRetryHelper:
    def test_retries_transient_then_succeeds(self):
        calls = {"n": 0}

        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("temporary")
            return {"ok": True}

        result = ingest_agent._gemini_with_retry_generic(method, "abc")
        assert result == {"ok": True}
        assert calls["n"] == 3

    def test_raises_last_transient_after_max_retries(self):
        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            raise ConnectionError("network")

        with pytest.raises(ConnectionError):
            ingest_agent._gemini_with_retry_generic(method, "abc")

    def test_non_transient_is_not_retried(self):
        calls = {"n": 0}

        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            calls["n"] += 1
            raise ValueError("bad schema")

        with pytest.raises(ValueError):
            ingest_agent._gemini_with_retry_generic(method, "abc")
        assert calls["n"] == 1


class TestIngestNodeRoutes:
    def test_skips_when_upstream_error_exists(self, pdf_file):
        state = base_state(file_path=pdf_file, error="upstream")
        out = ingest_agent.ingest_node(state)
        assert out["error"] == "upstream"

    def test_unsupported_extension_sets_error(self, tmp_path: Path):
        p = tmp_path / "file.txt"
        p.write_text("hello", encoding="utf-8")
        state = base_state(file_path=str(p))

        out = ingest_agent.ingest_node(state)

        assert out["error"] is not None
        assert "Unsupported file format" in out["error"]
        assert any(e["event"] == "node_error" for e in out["agent_log"])

    def test_xlsx_extracts_with_parser(self, xlsx_file, monkeypatch):
        monkeypatch.setattr(
            "app.services.excel_parser.parse_excel",
            lambda _: ("xlsx extracted text for tests", [{"sheet": "A"}]),
        )
        client = _build_client("extract_transactions", {"any": "value"})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=xlsx_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert out["parsed_content"] == [{"sheet": "A"}]
        assert out["raw_text"] == "xlsx extracted text for tests"
        assert out["result"]["status"] == "completed"

    def test_xlsx_reuses_state_raw_text_without_parsing(self, xlsx_file, monkeypatch):
        parse_mock = MagicMock(return_value=("will not be used", []))
        monkeypatch.setattr("app.services.excel_parser.parse_excel", parse_mock)

        client = _build_client("extract_transactions", {"ok": 1})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(
            file_path=xlsx_file, raw_text="already extracted xlsx content"
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        parse_mock.assert_not_called()

    def test_xml_path_uses_xml_parser(self, xml_file, monkeypatch):
        monkeypatch.setattr(
            "app.services.xml_parser.parse_xml", lambda _: "xml text for tests"
        )
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=xml_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert out["raw_text"] == "xml text for tests"

    def test_pdf_without_llamaparse_sets_error(self, pdf_file, monkeypatch):
        monkeypatch.setattr(ingest_agent, "LlamaParse", None)

        state = base_state(file_path=pdf_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is not None
        assert out["error"].startswith("Ingest error:")
        assert out["result"]["status"] == "error"

    def test_pdf_cache_hit_skips_llamaparse(self, pdf_file, monkeypatch):
        cache_dir = Path(pdf_file).parent / ".parse_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(pdf_file).name.replace(" ", "_")
        cache_file = cache_dir / f"{safe_name}.md"
        cache_file.write_text("cached parsed text for ingest", encoding="utf-8")

        llama_cls = MagicMock()
        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)
        monkeypatch.setattr(
            ingest_agent,
            "get_settings",
            lambda: SimpleNamespace(llama_cloud_api_key="k"),
        )
        client = _build_client("extract_transactions", {"ok": 1})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=pdf_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert out["raw_text"] == "cached parsed text for ingest"
        llama_cls.assert_not_called()

    def test_pdf_markdown_empty_falls_back_to_text_and_caches(
        self, pdf_file, monkeypatch
    ):
        first_parser = MagicMock()
        first_doc = MagicMock()
        first_doc.text = ""
        first_parser.load_data.return_value = [first_doc]

        second_parser = MagicMock()
        second_doc = MagicMock()
        second_doc.text = "text mode output long enough for ingestion"
        second_parser.load_data.return_value = [second_doc]

        llama_cls = MagicMock(side_effect=[first_parser, second_parser])

        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)
        monkeypatch.setattr(
            ingest_agent,
            "get_settings",
            lambda: SimpleNamespace(llama_cloud_api_key="k"),
        )
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=pdf_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert llama_cls.call_count == 2

        cache_file = (
            Path(pdf_file).parent
            / ".parse_cache"
            / f"{Path(pdf_file).name.replace(' ', '_')}.md"
        )
        assert cache_file.exists()
        assert "text mode output" in cache_file.read_text(encoding="utf-8")

    def test_empty_extracted_text_sets_readable_error(self, xml_file, monkeypatch):
        monkeypatch.setattr("app.services.xml_parser.parse_xml", lambda _: "   ")
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=xml_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] == "No readable text found in document"
        assert any(e["event"] == "node_error" for e in out["agent_log"])

    def test_short_text_logs_warning_event(self, xml_file, monkeypatch):
        monkeypatch.setattr("app.services.xml_parser.parse_xml", lambda _: "short text")
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=xml_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert any(e["event"] == "short_text_warning" for e in out["agent_log"])

    def test_dispatch_error_when_method_missing_for_doc_type(
        self, xml_file, monkeypatch
    ):
        monkeypatch.setattr(
            "app.services.xml_parser.parse_xml",
            lambda _: "text long enough for dispatch",
        )
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: SimpleNamespace())

        state = base_state(
            file_path=xml_file,
            document_classification={"doc_type": "factura_venta"},
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is not None
        assert "dispatch error" in out["error"]

    def test_non_dict_gemini_output_sets_error(self, xml_file, monkeypatch):
        monkeypatch.setattr(
            "app.services.xml_parser.parse_xml", lambda _: "text long enough for output"
        )
        client = _build_client("extract_transactions", [1, 2, 3])
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=xml_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is not None
        assert "expected dict" in out["error"]

    def test_retry_reuses_raw_text_and_passes_correction_feedback(
        self, pdf_file, monkeypatch
    ):
        llama_cls = MagicMock()
        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)

        client = _build_client("extract_factura_venta", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(
            file_path=pdf_file,
            raw_text="already extracted raw content for retry path",
            correction_feedback="fix schema fields",
            document_classification={"doc_type": "factura_venta"},
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert out["correction_feedback"] is None
        client.extract_factura_venta.assert_called_once_with(
            "already extracted raw content for retry path",
            correction_feedback="fix schema fields",
        )
        llama_cls.assert_not_called()

    def test_dispatch_logs_via_b_pathway_hint(self, xml_file, monkeypatch):
        monkeypatch.setattr(
            "app.services.xml_parser.parse_xml",
            lambda _: "xml content long enough for via b",
        )
        client = _build_client("extract_balance_general", {"statement": "ok"})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(
            file_path=xml_file,
            document_classification={"doc_type": "balance_general"},
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        dispatch = [e for e in out["agent_log"] if e["event"] == "dispatch_selected"]
        assert dispatch
        assert dispatch[-1]["details"]["pathway_hint"] == "work_with_existing"

    def test_ingest_exception_sets_error_result(self, xml_file, monkeypatch):
        monkeypatch.setattr(
            "app.services.xml_parser.parse_xml", lambda _: "xml content long enough"
        )

        client = MagicMock()
        client.extract_transactions.side_effect = RuntimeError("gemini exploded")
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=xml_file)
        out = ingest_agent.ingest_node(state)

        assert out["error"] is not None
        assert out["result"]["status"] == "error"
        assert "gemini exploded" in out["error"]
