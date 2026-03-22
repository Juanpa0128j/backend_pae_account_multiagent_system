"""
Integration tests for the full agent graph pipeline.

All external dependencies (Gemini API, LlamaParse, PostgreSQL) are mocked so
tests run without any network or database connectivity.

Coverage:
1. Graph structure — compiles and has expected nodes
2. Happy path — PDF → extract → Gemini → validate → DB persist (mocked)
3. Validation retry — invalid then valid output
4. Exhausted retries — always invalid → error state
5. DB persist — verify mock was called + state has expected db_result
6. Error propagation — upstream errors skip downstream nodes
7. invoke_ingest_pipeline wrapper
8. API endpoint /upload
"""

import os
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock, call

from app.agents.graph import create_agent_graph, invoke_ingest_pipeline
from app.agents.state import AgentState


# ─── Constants ────────────────────────────────────────────────────────────────

SAMPLE_RAW_TEXT = """
--- PAGE 1 ---
FACTURA DE VENTA No. FV-2026-0042
Fecha: 15/01/2026
NIT Emisor: 900.123.456-7
Razón Social: ContaExpress SAS

Concepto: Servicio de consultoría contable mensual
Subtotal: $1,215,000
IVA 19%: $285,000
Retención fuente: $150,000
ReteICA: $15,000
TOTAL: $1,500,000
Neto a pagar: $1,335,000
"""

# Valid interpreted data that the validation engine will accept
VALID_INTERPRETED_DATA = {
    "fecha": "2026-01-15",
    "monto": 1500000.0,
    "concepto": "Servicio de consultoría contable mensual",
    "beneficiario": "ContaExpress SAS",
    "empresa": "Bancolombia",
    "referencia": "REF-20260115-001",
    "tipo_documento": "factura",
    "total": 1500000,
    "valor_total": 1500000,
    "nit_emisor": "900123456",
    "nit_receptor": "800999888",
    "descripcion": "Consultoría contable enero 2026",
    "iva": 285000,
    "retefuente": 150000,
    "reteica": 15000,
    "neto_a_pagar": 1335000,
    "cuenta_puc": "5110",
    "cuenta_nombre": "Honorarios",
    "items": [
        {"descripcion": "Consultoría contable", "cantidad": 1, "valor": 1500000}
    ],
}

# Invalid output — missing required fields / bad values
INVALID_GEMINI_OUTPUT = {
    "fecha": "bad-date",
    "monto": -100,
    "concepto": "",
}

# Mock classification result
MOCK_CLASSIFICATION = {
    "doc_type": "factura_venta",
    "confidence": 0.95,
    "pathway": "BUILD_FROM_SCRATCH",
    "source_format": "pdf",
    "notes": "Electronic sales invoice",
}

# Mock DB persist result
MOCK_DB_RESULT = {
    "ingest_id": "test-ingest-uuid-001",
    "transaction_pending_id": "test-pending-uuid-001",
    "transaction_posted_id": "test-posted-uuid-001",
    "journal_lines_count": 4,
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

_retry_counter: dict = {"calls": 0}


def _make_mock_llama_parse(text: str = SAMPLE_RAW_TEXT):
    """Return a patched LlamaParse class that yields canned markdown text."""
    mock_doc = MagicMock()
    mock_doc.text = text
    mock_parser_instance = MagicMock()
    mock_parser_instance.load_data.return_value = [mock_doc]
    MockLlamaParse = MagicMock(return_value=mock_parser_instance)
    return MockLlamaParse


def _make_mock_gemini_client(side_effect=None, return_value=None):
    """Return a mock get_gemini_client() factory and its underlying client."""
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client.extract_factura_venta.side_effect = side_effect
    else:
        mock_client.extract_factura_venta.return_value = return_value or VALID_INTERPRETED_DATA.copy()
    mock_get_client = MagicMock(return_value=mock_client)
    return mock_get_client, mock_client


def _make_mock_classifier():
    """Return a patched classify_document function."""
    mock_cls = MagicMock()
    mock_result = MagicMock()
    mock_result.model_dump.return_value = MOCK_CLASSIFICATION
    mock_result.pathway.value = "BUILD_FROM_SCRATCH"
    mock_cls.return_value = mock_result
    return mock_cls


def _make_mock_db_service():
    """Return a mock db_service module with relevant methods configured."""
    mock_svc = MagicMock()
    mock_job = MagicMock()
    mock_job.id = "test-ingest-uuid-001"
    mock_svc.create_ingest_job.return_value = mock_job
    mock_svc.update_ingest_job.return_value = mock_job
    mock_svc.create_transaction_pending.return_value = MagicMock(id="test-pending-uuid-001")
    mock_svc.create_transaction_posted.return_value = MagicMock(id="test-posted-uuid-001")
    mock_svc.create_journal_lines.return_value = []
    return mock_svc


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def initial_state(tmp_path) -> AgentState:
    """Build a valid initial AgentState with a real (dummy) PDF path."""
    dummy_pdf = tmp_path / "factura_test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content for testing")
    return {
        "file_path": str(dummy_pdf),
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
    }


def _make_mock_persist_node():
    """Return a mock db_persist_node that sets a db_result on state without hitting DB."""
    def _mock_persist(state):
        state["db_result"] = {
            "ingest_id": "test-ingest-uuid-001",
            "transaction_pending_id": "test-pending-uuid-001",
            "transaction_posted_id": "test-posted-uuid-001",
            "journal_lines_count": 4,
        }
        return state
    return _mock_persist


@pytest.fixture()
def full_pipeline_patches():
    """
    Patch all external dependencies for a full pipeline run:
    - LlamaParse (PDF/image text extraction)
    - get_gemini_client (LLM interpretation)
    - classify_document (doc type detection)
    - db_persist_node (entire persist step replaced with no-op mock)
    """
    mock_llama_cls = _make_mock_llama_parse()
    mock_get_client, mock_client = _make_mock_gemini_client()
    mock_classifier = _make_mock_classifier()
    mock_persist = _make_mock_persist_node()
    mock_persist_spy = MagicMock(side_effect=mock_persist)

    with (
        patch("app.agents.ingest_agent.LlamaParse", mock_llama_cls),
        patch("app.agents.ingest_agent.get_gemini_client", mock_get_client),
        patch("app.services.doc_classifier.classify_document", mock_classifier),
        patch("app.agents.graph.db_persist_node", mock_persist_spy),
    ):
        yield {
            "llama_parse": mock_llama_cls,
            "get_gemini_client": mock_get_client,
            "gemini_client": mock_client,
            "classifier": mock_classifier,
            "db_persist_node": mock_persist_spy,
        }


# ─── TEST: Graph Structure ─────────────────────────────────────────────────────

class TestGraphStructure:
    """Verify graph compiles and has expected nodes/edges — no external I/O."""

    def test_graph_compiles(self):
        graph = create_agent_graph()
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        graph = create_agent_graph()
        node_names = [n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")]
        assert "supervisor" in node_names
        assert "ingesta" in node_names
        assert "validate_output" in node_names
        assert "db_persist" in node_names

    def test_graph_node_count(self):
        graph = create_agent_graph()
        node_names = [n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")]
        assert len(node_names) == 10


# ─── TEST: Happy Path ─────────────────────────────────────────────────────────

class TestHappyPath:
    """Full pipeline run with all externals mocked."""

    def test_full_pipeline_success(self, initial_state, full_pipeline_patches):
        """Ingest → interpret → validate → persist all succeed."""
        # Configure persist node to report db_result in state
        patches = full_pipeline_patches

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        # LlamaParse was called
        patches["llama_parse"].assert_called_once()

        # Gemini was called
        patches["get_gemini_client"].assert_called()

        # No error
        assert final_state.get("error") is None, f"Unexpected error: {final_state.get('error')}"

        # Raw text is populated
        assert final_state.get("raw_text") is not None
        assert len(final_state["raw_text"]) > 0

        # interpreted_data was set
        assert final_state.get("interpreted_data") is not None

        # Result status
        assert final_state["result"].get("status") == "completed"

        # Persist node was invoked
        patches["db_persist_node"].assert_called_once()

    def test_raw_text_extracted(self, initial_state, full_pipeline_patches):
        """Raw text from LlamaParse lands in state."""
        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert "FACTURA DE VENTA" in final_state["raw_text"]
        assert "900.123.456-7" in final_state["raw_text"]

    def test_validation_history_populated(self, initial_state, full_pipeline_patches):
        """Validation node records at least one validation run."""
        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        history = final_state.get("validation_history", [])
        assert len(history) >= 1
        assert history[-1]["is_valid"] is True


# ─── TEST: Validation Retry Flow ──────────────────────────────────────────────

class TestValidationRetry:
    """Tests the retry loop: invalid output → correction_feedback → retry → success."""

    def test_retry_then_success(self, initial_state, full_pipeline_patches):
        """First Gemini call returns invalid data, second returns valid."""
        _retry_counter["calls"] = 0
        patches = full_pipeline_patches

        def _fail_then_succeed(text: str, *, correction_feedback=None) -> dict:
            _retry_counter["calls"] += 1
            if _retry_counter["calls"] <= 1:
                return INVALID_GEMINI_OUTPUT.copy()
            return VALID_INTERPRETED_DATA.copy()

        patches["gemini_client"].extract_factura_venta.side_effect = _fail_then_succeed

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert final_state.get("error") is None, f"Unexpected error: {final_state.get('error')}"

        history = final_state.get("validation_history", [])
        assert len(history) >= 2
        assert history[0]["is_valid"] is False
        assert history[-1]["is_valid"] is True

    def test_exhausted_retries_error(self, initial_state, full_pipeline_patches):
        """All retries fail → error state."""
        patches = full_pipeline_patches
        patches["gemini_client"].extract_factura_venta.return_value = INVALID_GEMINI_OUTPUT.copy()
        # Clear any side_effect
        patches["gemini_client"].extract_factura_venta.side_effect = None

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert final_state.get("error") is not None

        history = final_state.get("validation_history", [])
        assert len(history) >= 2
        assert all(not h["is_valid"] for h in history)

        assert final_state.get("db_result") is None


# ─── TEST: DB Persist (Mocked) ────────────────────────────────────────────────

class TestDbPersistIntegration:
    """Verify db_service interactions after a successful pipeline run."""

    def test_db_service_called_after_success(self, initial_state, full_pipeline_patches):
        """db_persist_node should be called after a successful pipeline run."""
        patches = full_pipeline_patches

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        # Persist node was invoked
        assert patches["db_persist_node"].called
        # db_result set by mock persist node
        assert final_state.get("db_result") is not None
        assert final_state["db_result"]["ingest_id"] == "test-ingest-uuid-001"

    def test_no_db_calls_on_error(self, initial_state, full_pipeline_patches):
        """If pipeline errors early, db_persist_node should not be called."""
        patches = full_pipeline_patches

        # Make LlamaParse return empty text to trigger an error
        mock_doc = MagicMock()
        mock_doc.text = ""
        mock_parser = MagicMock()
        mock_parser.load_data.return_value = [mock_doc]
        patches["llama_parse"].return_value = mock_parser

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert final_state.get("error") is not None
        # persist node should not have been called
        assert not patches["db_persist_node"].called


# ─── TEST: Error Propagation ──────────────────────────────────────────────────

class TestErrorPropagation:
    """Verify errors in early nodes skip downstream processing."""

    def test_file_not_found_skips_pipeline(self):
        """Non-existent file → supervisor error → skip ingest/validate/persist."""
        state: AgentState = {
            "file_path": "/nonexistent/path/fake.pdf",
            "raw_text": "",
            "interpreted_data": {},
            "result": {},
            "error": None,
            "validation_history": [],
            "current_agent": "",
            "correction_feedback": None,
            "retry_count": 0,
            "ingest_id": None,
            "db_result": None,
        }

        graph = create_agent_graph()
        final_state = graph.invoke(state)

        assert final_state.get("error") is not None
        error_lower = final_state["error"].lower()
        assert "not found" in error_lower or "does not exist" in error_lower or "file" in error_lower
        assert final_state.get("db_result") is None

    def test_non_pdf_file_error(self, tmp_path):
        """Non-supported file extension → supervisor error."""
        txt_file = tmp_path / "document.txt"
        txt_file.write_text("not a pdf")

        state: AgentState = {
            "file_path": str(txt_file),
            "raw_text": "",
            "interpreted_data": {},
            "result": {},
            "error": None,
            "validation_history": [],
            "current_agent": "",
            "correction_feedback": None,
            "retry_count": 0,
            "ingest_id": None,
            "db_result": None,
        }

        graph = create_agent_graph()
        final_state = graph.invoke(state)

        assert final_state.get("error") is not None
        assert final_state.get("db_result") is None

    def test_gemini_exception_produces_error(self, initial_state, full_pipeline_patches):
        """If Gemini throws, ingest catches it and sets error."""
        patches = full_pipeline_patches
        patches["gemini_client"].extract_factura_venta.side_effect = RuntimeError(
            "Gemini API quota exceeded"
        )

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert final_state.get("error") is not None
        error_text = final_state["error"]
        assert "Gemini API quota" in error_text or "Ingest error" in error_text


# ─── TEST: invoke_ingest_pipeline wrapper ─────────────────────────────────────

class TestInvokeAgent:
    """Test the invoke_ingest_pipeline() convenience function."""

    def test_invoke_agent_returns_result(self, tmp_path, full_pipeline_patches):
        """invoke_ingest_pipeline() should return a dict with status."""
        dummy_pdf = tmp_path / "test_invoice.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

        result = invoke_ingest_pipeline(str(dummy_pdf))

        assert isinstance(result, dict)
        assert result.get("status") == "completed"

    def test_invoke_agent_includes_validation_history(self, tmp_path, full_pipeline_patches):
        """Result should include validation_history."""
        dummy_pdf = tmp_path / "invoice2.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

        result = invoke_ingest_pipeline(str(dummy_pdf))

        assert "validation_history" in result


# ─── TEST: API Endpoint ───────────────────────────────────────────────────────

class TestApiEndpoint:
    """Test the /api/v1/ingest/upload endpoint."""

    @pytest.fixture(autouse=True)
    def _add_root_to_path(self):
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)

    @patch("app.api.v1.ingest.invoke_ingest_pipeline")
    @patch("app.api.v1.ingest.db_service")
    def test_upload_endpoint_returns_202(self, mock_db_svc, mock_invoke):
        """POST /upload with a PDF should return 202 Accepted."""
        from fastapi.testclient import TestClient
        from main import app  # noqa: E402

        mock_job = MagicMock()
        mock_job.id = "ingest-test-123"
        mock_job.status.value = "pending_processing"
        mock_job.created_at = None
        mock_db_svc.create_ingest_job.return_value = mock_job

        mock_invoke.return_value = {
            "status": "completed",
            "message": "Receipt processed",
            "ingest_id": "ingest-test-123",
            "process_id": "proc_123",
            "validation_history": [],
        }

        client = TestClient(app)
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 0\ntrailer\n<< >>\nstartxref\n0\n%%EOF"

        response = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("factura_test.pdf", pdf_content, "application/pdf")},
        )

        assert response.status_code == 202
        data = response.json()
        assert "ingest_id" in data

    @patch("app.api.v1.ingest.db_service")
    def test_upload_rejects_non_pdf(self, mock_db_svc):
        """Non-supported files should be rejected with 422."""
        from fastapi.testclient import TestClient
        from main import app  # noqa: E402

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("document.txt", b"hello world", "text/plain")},
        )

        assert response.status_code == 422

    @patch("app.api.v1.ingest.db_service")
    def test_upload_accepts_jpg(self, mock_db_svc):
        """JPG image files should be accepted."""
        from fastapi.testclient import TestClient
        from main import app  # noqa: E402

        mock_job = MagicMock()
        mock_job.id = "ingest-jpg-123"
        mock_job.status.value = "pending_processing"
        mock_job.created_at = None
        mock_db_svc.create_ingest_job.return_value = mock_job

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("voucher.jpg", b"\xff\xd8\xff\xe0test", "image/jpeg")},
        )

        assert response.status_code == 202
