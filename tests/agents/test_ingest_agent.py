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
from app.agents.llm_retry import llm_with_parse_retry  # noqa: E402
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

        result = llm_with_parse_retry(method, "abc")
        assert result == {"ok": True}
        assert calls["n"] == 3

    def test_raises_last_transient_after_max_retries(self):
        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            raise ConnectionError("network")

        with pytest.raises(ConnectionError):
            llm_with_parse_retry(method, "abc")

    def test_non_transient_is_not_retried(self):
        calls = {"n": 0}

        def method(raw_text, correction_feedback=None):
            _ = (raw_text, correction_feedback)
            calls["n"] += 1
            raise ValueError("bad schema")

        with pytest.raises(ValueError):
            llm_with_parse_retry(method, "abc")
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
        import hashlib

        cache_dir = Path(pdf_file).parent / ".parse_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Cache is keyed by content hash, not filename, so two uploads of
        # different files with the same name don't collide.
        content_hash = hashlib.sha256(Path(pdf_file).read_bytes()).hexdigest()
        cache_file = cache_dir / f"{content_hash}.fast.md"
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

        import hashlib

        content_hash = hashlib.sha256(Path(pdf_file).read_bytes()).hexdigest()
        cache_file = Path(pdf_file).parent / ".parse_cache" / f"{content_hash}.fast.md"
        assert cache_file.exists()
        assert "text mode output" in cache_file.read_text(encoding="utf-8")

    def test_pdf_fallback_parse_failure_sets_error_instead_of_crashing(
        self, pdf_file, monkeypatch
    ):
        """Regression: LlamaCloud can return a job result missing the requested
        key (raw KeyError from llama_parse's base.py), and it can do so on the
        text-mode fallback too, not just the first markdown attempt. The
        fallback call must be caught and translated to state["error"], not
        left to crash the pipeline uncaught."""
        first_parser = MagicMock()
        first_doc = MagicMock()
        first_doc.text = ""
        first_parser.load_data.return_value = [first_doc]

        second_parser = MagicMock()
        second_parser.load_data.side_effect = KeyError("markdown")

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

        assert out["error"] is not None
        assert "both markdown and text mode" in out["error"]

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

    def test_llamaparse_fast_mode_uses_fast_flag(self, pdf_file, monkeypatch):
        parser_instance = MagicMock()
        doc = MagicMock()
        doc.text = "fake parsed text"
        parser_instance.load_data.return_value = [doc]

        llama_cls = MagicMock(return_value=parser_instance)
        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)
        monkeypatch.setattr(
            ingest_agent,
            "get_settings",
            lambda: SimpleNamespace(llama_cloud_api_key="k"),
        )
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=pdf_file, parser_mode="fast")
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert llama_cls.call_count == 1
        assert llama_cls.call_args.kwargs.get("fast_mode") is True

    def test_llamaparse_premium_mode_uses_premium_flag(self, pdf_file, monkeypatch):
        parser_instance = MagicMock()
        doc = MagicMock()
        doc.text = "fake parsed text"
        parser_instance.load_data.return_value = [doc]

        llama_cls = MagicMock(return_value=parser_instance)
        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)
        monkeypatch.setattr(
            ingest_agent,
            "get_settings",
            lambda: SimpleNamespace(llama_cloud_api_key="k"),
        )
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=pdf_file, parser_mode="premium")
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert llama_cls.call_count == 1
        assert llama_cls.call_args.kwargs.get("premium_mode") is True

    def test_llamaparse_gpt4o_mode_uses_gpt4o_flag(self, pdf_file, monkeypatch):
        parser_instance = MagicMock()
        doc = MagicMock()
        doc.text = "fake parsed text"
        parser_instance.load_data.return_value = [doc]

        llama_cls = MagicMock(return_value=parser_instance)
        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)
        monkeypatch.setattr(
            ingest_agent,
            "get_settings",
            lambda: SimpleNamespace(llama_cloud_api_key="k"),
        )
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=pdf_file, parser_mode="gpt4o")
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert llama_cls.call_count == 1
        assert llama_cls.call_args.kwargs.get("gpt4o_mode") is True

    def test_llamaparse_standard_mode_uses_no_extra_flags(self, pdf_file, monkeypatch):
        parser_instance = MagicMock()
        doc = MagicMock()
        doc.text = "fake parsed text"
        parser_instance.load_data.return_value = [doc]

        llama_cls = MagicMock(return_value=parser_instance)
        monkeypatch.setattr(ingest_agent, "LlamaParse", llama_cls)
        monkeypatch.setattr(
            ingest_agent,
            "get_settings",
            lambda: SimpleNamespace(llama_cloud_api_key="k"),
        )
        client = _build_client("extract_transactions", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(file_path=pdf_file, parser_mode="standard")
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert llama_cls.call_count == 1
        kwargs = llama_cls.call_args.kwargs
        assert "fast_mode" not in kwargs
        assert "premium_mode" not in kwargs
        assert "gpt4o_mode" not in kwargs


class TestIngestNodeMultiPage:
    def test_ingest_node_parses_multiple_files_and_concatenates(self, monkeypatch):
        """When file_paths has multiple items, parse each and concatenate with page separators."""

        def _fake_parse_xml(path: str) -> str:
            return f"xml text from {Path(path).name}"

        monkeypatch.setattr("app.services.xml_parser.parse_xml", _fake_parse_xml)
        client = _build_client("extract_factura_venta", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(
            file_path="/tmp/page1.xml",
            file_paths=["/tmp/page1.xml", "/tmp/page2.xml", "/tmp/page3.xml"],
            document_classification={"doc_type": "factura_venta"},
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        expected = (
            "xml text from page1.xml\n\n--- PAGE 1 ---\n\n"
            "xml text from page2.xml\n\n--- PAGE 2 ---\n\n"
            "xml text from page3.xml"
        )
        assert out["raw_text"] == expected
        client.extract_factura_venta.assert_called_once_with(
            expected, correction_feedback=None
        )

    def test_ingest_node_fallback_to_single_file_path(self, monkeypatch):
        """When file_paths is empty, fall back to parsing file_path (backward compat)."""
        monkeypatch.setattr(
            "app.services.xml_parser.parse_xml", lambda _: "single xml text"
        )
        client = _build_client("extract_factura_venta", {"ok": True})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(
            file_path="/tmp/single.xml",
            file_paths=[],
            document_classification={"doc_type": "factura_venta"},
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert out["raw_text"] == "single xml text"
        client.extract_factura_venta.assert_called_once_with(
            "single xml text", correction_feedback=None
        )

    def test_ingest_node_pages_mode_concatenates(self, monkeypatch):
        """multi_file_mode='pages' (default) concatenates all files into one LLM call."""

        def _fake_parse_xml(path: str) -> str:
            return f"text from {Path(path).name}"

        monkeypatch.setattr("app.services.xml_parser.parse_xml", _fake_parse_xml)
        client = _build_client("extract_factura_venta", {"factura": "ok"})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        state = base_state(
            file_path="/tmp/f1.xml",
            file_paths=["/tmp/f1.xml", "/tmp/f2.xml"],
            document_classification={"doc_type": "factura_venta"},
            multi_file_mode="pages",
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        # One combined LLM call
        assert client.extract_factura_venta.call_count == 1
        raw = out["raw_text"]
        assert "text from f1.xml" in raw
        assert "text from f2.xml" in raw

    def test_ingest_node_documents_mode_calls_llm_per_file(self, monkeypatch):
        """multi_file_mode='documents' calls LLM once per file and merges results."""
        call_texts = []

        def _fake_parse_xml(path: str) -> str:
            return f"text from {Path(path).name}"

        monkeypatch.setattr("app.services.xml_parser.parse_xml", _fake_parse_xml)

        def _fake_extract(raw_text, correction_feedback=None):
            call_texts.append(raw_text)
            return {"items": [{"desc": raw_text[:20]}]}

        client = MagicMock()
        client.extract_factura_venta = _fake_extract
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)
        # No DB calls in this test — no ingest_id
        state = base_state(
            file_path="/tmp/f1.xml",
            file_paths=["/tmp/f1.xml", "/tmp/f2.xml", "/tmp/f3.xml"],
            document_classification={"doc_type": "factura_venta"},
            multi_file_mode="documents",
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        # LLM called once per file
        assert len(call_texts) == 3
        assert call_texts[0] == "text from f1.xml"
        assert call_texts[1] == "text from f2.xml"
        assert call_texts[2] == "text from f3.xml"
        # interpreted_data has merged items list
        merged = out["interpreted_data"]
        assert isinstance(merged.get("items"), list)
        assert len(merged["items"]) == 3

    def test_ingest_node_documents_mode_updates_current_file_index(self, monkeypatch):
        """In documents mode, current_file_index is updated in DB before each file."""
        updated_indices = []

        def _fake_parse_xml(path: str) -> str:
            return f"text from {Path(path).name}"

        monkeypatch.setattr("app.services.xml_parser.parse_xml", _fake_parse_xml)
        client = _build_client("extract_factura_venta", {"items": []})
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        def _fake_update_index(db, ingest_id, index):
            updated_indices.append(index)

        monkeypatch.setattr(
            "app.services.db_service.update_ingest_file_index", _fake_update_index
        )
        # Patch SessionLocal to return a dummy db
        dummy_db = MagicMock()
        monkeypatch.setattr("app.core.database.SessionLocal", lambda: dummy_db)

        state = base_state(
            file_path="/tmp/f1.xml",
            file_paths=["/tmp/f1.xml", "/tmp/f2.xml"],
            document_classification={"doc_type": "factura_venta"},
            multi_file_mode="documents",
            ingest_id="ing_test_123",
        )
        out = ingest_agent.ingest_node(state)

        assert out["error"] is None
        assert updated_indices == [0, 1]
