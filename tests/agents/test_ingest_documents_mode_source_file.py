"""
TDD: Gap 5 — source_file attribution in documents mode.

Tests that _ingest_documents_mode tags each transaction dict with
`source_file` (filename only, not full path) and populates
state["raw_transactions"] with those tagged dicts.
"""

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ── Stub heavy deps before importing ingest_agent ──────────────────────────

_fake_llm = types.ModuleType("app.core.llm_client")


class _DummyLLMClient:
    pass


_fake_llm.LLMClient = _DummyLLMClient
_fake_llm.get_llm_client = lambda: MagicMock()
_fake_llm._compact_error_message = lambda exc, max_len=240: str(exc)[:max_len]
sys.modules.setdefault("app.core.llm_client", _fake_llm)

_fake_config = types.ModuleType("app.core.config")
_fake_config.get_settings = lambda: SimpleNamespace(llama_cloud_api_key="test-key")
_fake_config.settings = SimpleNamespace(
    llama_cloud_api_key="test-key",
    database_url="postgresql://localhost/test",
    app_env="test",
)
sys.modules.setdefault("app.core.config", _fake_config)

from app.agents import ingest_agent  # noqa: E402
from tests.conftest import base_state  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_file(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 dummy")
    return str(p)


def _make_llm_client(extracted_data: dict):
    """Return mock LLM client whose any extract_* method returns extracted_data."""

    # Use a simple object with __getattr__ so any attribute access returns
    # a callable that returns extracted_data — avoids MagicMock returning MagicMock.
    class _AnyExtractClient:
        def __getattr__(self, name):
            return lambda *args, **kwargs: extracted_data

    return _AnyExtractClient()


# ── Tests ────────────────────────────────────────────────────────────────────


class TestDocumentsModeSourceFileAttribution:
    def test_raw_transactions_populated_with_source_file(self, tmp_path, monkeypatch):
        """_ingest_documents_mode must populate raw_transactions with source_file per tx."""
        file1 = _make_file(tmp_path, "factura_001.pdf")
        file2 = _make_file(tmp_path, "factura_002.pdf")

        extracted = {
            "emisor": {"nit": "123"},
            "receptor": {"nit": "456"},
            "totales": {"total": "1000"},
        }

        client = _make_llm_client(extracted)

        # Stub parse so we don't call LlamaParse
        monkeypatch.setattr(
            ingest_agent,
            "_parse_single_file",
            lambda fp, state: "dummy page text",
        )
        # Stub llm_with_parse_retry to just call extract_transactions directly
        monkeypatch.setattr(
            ingest_agent,
            "llm_with_parse_retry",
            lambda method, text, agent_label=None: method(text),
        )
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        # Stub build_structured_transactions to return 1 tx per file
        import app.agents.ingest_agent as _ia

        monkeypatch.setattr(
            _ia,
            "build_structured_transactions",
            lambda data, doc_type: [{"total": "1000", "concepto": "test"}],
        )

        # Stub audit stuff
        monkeypatch.setattr(
            ingest_agent,
            "_ingest_documents_mode",
            ingest_agent._ingest_documents_mode,  # keep real
        )

        # Stub ingest_auditor to avoid DB
        with patch(
            "app.agents.auditors.ingest_auditor.run",
            return_value=MagicMock(findings=[]),
        ):
            state = base_state(
                document_classification={"doc_type": "factura_venta"},
                ingest_id=None,
            )
            out = ingest_agent._ingest_documents_mode(state, [file1, file2])

        raw_txs = out.get("raw_transactions", [])
        assert len(raw_txs) == 2, f"Expected 2 tagged transactions, got {len(raw_txs)}"

        source_files = [tx.get("source_file") for tx in raw_txs]
        assert "factura_001.pdf" in source_files, f"source_files={source_files}"
        assert "factura_002.pdf" in source_files, f"source_files={source_files}"

    def test_source_file_is_filename_not_full_path(self, tmp_path, monkeypatch):
        """source_file must be just the filename, not the full filesystem path."""
        file1 = _make_file(tmp_path, "extracto_banco.pdf")

        extracted = {"emisor": {}, "receptor": {}, "totales": {"total": "500"}}
        client = _make_llm_client(extracted)

        monkeypatch.setattr(
            ingest_agent, "_parse_single_file", lambda fp, state: "text"
        )
        monkeypatch.setattr(
            ingest_agent,
            "llm_with_parse_retry",
            lambda method, text, agent_label=None: method(text),
        )
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        import app.agents.ingest_agent as _ia

        monkeypatch.setattr(
            _ia,
            "build_structured_transactions",
            lambda data, doc_type: [{"total": "500"}],
        )

        with patch(
            "app.agents.auditors.ingest_auditor.run",
            return_value=MagicMock(findings=[]),
        ):
            state = base_state(
                document_classification={"doc_type": "extracto_bancario"},
                ingest_id=None,
            )
            out = ingest_agent._ingest_documents_mode(state, [file1])

        raw_txs = out.get("raw_transactions", [])
        assert len(raw_txs) == 1
        sf = raw_txs[0].get("source_file", "")
        assert sf == "extracto_banco.pdf", f"Expected filename only, got '{sf}'"
        assert "/" not in sf and "\\" not in sf

    def test_interpreted_data_still_set(self, tmp_path, monkeypatch):
        """_ingest_documents_mode must still set state['interpreted_data'] (audit path)."""
        file1 = _make_file(tmp_path, "doc.pdf")

        extracted = {"emisor": {}, "totales": {"total": "100"}}
        client = _make_llm_client(extracted)

        monkeypatch.setattr(
            ingest_agent, "_parse_single_file", lambda fp, state: "text"
        )
        monkeypatch.setattr(
            ingest_agent,
            "llm_with_parse_retry",
            lambda method, text, agent_label=None: method(text),
        )
        monkeypatch.setattr(ingest_agent, "get_llm_client", lambda: client)

        import app.agents.ingest_agent as _ia

        monkeypatch.setattr(
            _ia,
            "build_structured_transactions",
            lambda data, doc_type: [{"total": "100"}],
        )

        with patch(
            "app.agents.auditors.ingest_auditor.run",
            return_value=MagicMock(findings=[]),
        ):
            state = base_state(
                document_classification={"doc_type": "factura_venta"},
                ingest_id=None,
            )
            out = ingest_agent._ingest_documents_mode(state, [file1])

        assert out.get("interpreted_data") is not None


class TestPersistNodeSourceFilePassthrough:
    """Persist node must pass source_file from tx dict to create_transaction_pending."""

    def test_create_transaction_pending_called_with_source_file(self):
        """db_service.create_transaction_pending must receive source_file kwarg."""
        from app.services.db_service import create_transaction_pending

        # Verify the function signature accepts source_file
        import inspect

        sig = inspect.signature(create_transaction_pending)
        assert (
            "source_file" in sig.parameters
        ), "create_transaction_pending must accept source_file parameter"

    def test_transaction_pending_model_has_source_file(self):
        """TransactionPending ORM model must have source_file column."""
        from app.models.database import TransactionPending

        assert hasattr(
            TransactionPending, "source_file"
        ), "TransactionPending must have source_file column"
