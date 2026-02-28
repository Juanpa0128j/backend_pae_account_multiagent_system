"""
Integration tests for the full agent graph pipeline.

Tests the flow:  supervisor → ingesta → validate_output → db_persist

Gemini and PDF extraction are mocked so tests run without external
dependencies.  The real database is used (requires PostgreSQL running).

Coverage:
1. Happy path: PDF → extract text → Gemini interprets → validate → persist to DB
2. Validation retry: Gemini returns invalid data → validator rejects → retry → succeeds
3. Exhausted retries: 3 invalid outputs → hard failure
4. DB persist: interpreted data lands in all expected tables
5. API endpoint /upload end-to-end (with mocked agent)
6. Partida doble invariant after full pipeline
7. Error propagation: upstream errors skip downstream nodes
"""

import os
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.agents.graph import create_agent_graph, invoke_agent
from app.agents.state import AgentState
from app.models.database import (
    IngestJob,
    IngestStatus,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
    JournalEntryLine,
    AuditLog,
    CuentaPUC,
)
from app.services import db_service


# ─── Constants ───────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pae_user:password@localhost:5432/pae_accounting",
)

# Valid Gemini output matching IngestOutput schema
VALID_GEMINI_OUTPUT = {
    "fecha": "2026-01-15",
    "monto": 1500000.0,
    "concepto": "Servicio de consultoría contable mensual",
    "beneficiario": "ContaExpress SAS",
    "empresa": "Bancolombia",
    "referencia": "REF-20260115-001",
    "tipo_documento": "factura",
}

# Same data enriched with fields that db_persist_node reads
VALID_INTERPRETED_DATA = {
    **VALID_GEMINI_OUTPUT,
    "total": 1500000,
    "valor_total": 1500000,
    "nit_emisor": "900123456",
    "nit_receptor": "800999888",
    "descripcion": "Consultoría contable enero 2026",
    "concepto": "Consultoría contable enero 2026",
    "iva": 285000,       # 19% on base
    "retefuente": 150000,  # 10% retention
    "reteica": 15000,      # ~1%
    "neto_a_pagar": 1335000,
    "cuenta_puc": "5110",   # Honorarios
    "cuenta_nombre": "Honorarios",
    "items": [
        {"descripcion": "Consultoría contable", "cantidad": 1, "valor": 1500000}
    ],
}

# Invalid output — missing required fields
INVALID_GEMINI_OUTPUT = {
    "fecha": "bad-date",
    "monto": -100,
    "concepto": "",   # too short
    # missing: beneficiario, empresa, tipo_documento
}

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


# ─── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def engine():
    """Create a test database engine; skip test session if DB is unreachable."""
    eng = create_engine(DATABASE_URL, echo=False)
    try:
        conn = eng.connect()
        conn.close()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available at {DATABASE_URL!r}: {exc}")
    Base.metadata.create_all(bind=eng)
    yield eng


@pytest.fixture(scope="session")
def _session_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture()
def db(_session_factory):
    """Transactional test session — rolled back after each test."""
    session: Session = _session_factory()
    session.begin_nested()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def initial_state(tmp_path) -> AgentState:
    """Build a valid initial AgentState with a real (dummy) PDF path."""
    dummy_pdf = tmp_path / "factura_test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy content")
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


# ─── Helpers ─────────────────────────────────────────────────────

def _mock_extract_text(file_path: str) -> str:
    """Return canned raw text instead of actually reading a PDF."""
    return SAMPLE_RAW_TEXT


def _mock_gemini_valid(text: str, *, correction_feedback=None) -> dict:
    """Simulate Gemini returning valid structured data."""
    return VALID_INTERPRETED_DATA.copy()


_retry_counter = {"calls": 0}

def _mock_gemini_fail_then_succeed(text: str, *, correction_feedback=None) -> dict:
    """
    First call returns invalid data, second call returns valid data.
    Simulates a retry scenario.
    """
    _retry_counter["calls"] += 1
    if _retry_counter["calls"] <= 1:
        return INVALID_GEMINI_OUTPUT.copy()
    return VALID_INTERPRETED_DATA.copy()


def _mock_gemini_always_invalid(text: str, *, correction_feedback=None) -> dict:
    """Always returns invalid data — simulates exhausted retries."""
    return INVALID_GEMINI_OUTPUT.copy()


# ─── TEST: Graph Structure ───────────────────────────────────────

class TestGraphStructure:
    """Verify graph compiles and has expected nodes/edges."""

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
        assert len(node_names) == 4


# ─── TEST: Happy Path (Full Pipeline) ───────────────────────────

class TestHappyPath:
    """
    Full pipeline: PDF → extract → Gemini → validate → DB persist.
    Mocks: PDF extraction, Gemini API.
    Real: validation engine, db_persist, PostgreSQL.
    """

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch.object(
        __import__("app.core.gemini_client", fromlist=["GeminiClient"]).GeminiClient,
        "extract_receipt_data",
        side_effect=_mock_gemini_valid,
    )
    @patch(
        "app.agents.ingest_agent.GeminiClient",
    )
    def test_full_pipeline_success(self, MockGeminiCls, mock_gemini_method, mock_pdf, initial_state):
        """Run the full graph and verify success result + DB records."""
        # Set up mock GeminiClient
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        # Verify no error
        assert final_state.get("error") is None, f"Unexpected error: {final_state.get('error')}"

        # Verify interpreted_data was populated
        assert final_state["interpreted_data"] is not None
        assert final_state["interpreted_data"].get("total") == 1500000

        # Verify result contains success
        assert final_state["result"].get("status") == "completed"

        # Verify validation passed
        history = final_state.get("validation_history", [])
        assert len(history) >= 1
        assert history[-1]["is_valid"] is True

        # Verify DB persist result
        db_result = final_state.get("db_result")
        assert db_result is not None
        assert db_result["ingest_id"] is not None
        assert db_result["transaction_posted_id"] is not None
        assert db_result["journal_lines_count"] >= 2  # At least debit + credit

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_raw_text_extracted(self, MockGeminiCls, mock_pdf, initial_state):
        """Verify raw text is stored in state after ingest."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert "FACTURA DE VENTA" in final_state["raw_text"]
        assert "900.123.456-7" in final_state["raw_text"]


# ─── TEST: Validation Retry Flow ────────────────────────────────

class TestValidationRetry:
    """
    Tests the retry loop: invalid output → correction_feedback → retry → success.
    """

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_retry_then_success(self, MockGeminiCls, mock_pdf, initial_state):
        """First Gemini call returns invalid data, second returns valid."""
        _retry_counter["calls"] = 0  # Reset

        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_fail_then_succeed
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        # Should eventually succeed
        assert final_state.get("error") is None, f"Unexpected error: {final_state.get('error')}"

        # Validation history should have at least 2 entries (1 fail + 1 pass)
        history = final_state.get("validation_history", [])
        assert len(history) >= 2
        assert history[0]["is_valid"] is False  # First attempt failed
        assert history[-1]["is_valid"] is True   # Last attempt passed

        # DB should have been persisted
        assert final_state.get("db_result") is not None

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_exhausted_retries_error(self, MockGeminiCls, mock_pdf, initial_state):
        """All retries fail → error state with validation_error status."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_always_invalid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        # Should have an error after exhausting retries
        assert final_state.get("error") is not None
        assert "validation" in final_state["error"].lower() or "schema" in final_state["error"].lower()

        # Result should indicate validation_error
        assert final_state["result"].get("status") == "validation_error"

        # Validation history has multiple failed attempts
        history = final_state.get("validation_history", [])
        assert len(history) >= 2
        assert all(not h["is_valid"] for h in history)

        # DB persist should NOT have run (error skips it)
        assert final_state.get("db_result") is None


# ─── TEST: DB Persist Verification ──────────────────────────────

class TestDbPersistIntegration:
    """
    Verify that after a successful pipeline, all expected DB records exist.
    """

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_ingest_job_created(self, MockGeminiCls, mock_pdf, initial_state):
        """An IngestJob record should exist after pipeline."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        ingest_id = final_state["db_result"]["ingest_id"]

        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            job = db.query(IngestJob).filter(IngestJob.id == ingest_id).first()
            assert job is not None
            assert job.status == IngestStatus.COMPLETED
        finally:
            db.close()

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_transaction_pending_created(self, MockGeminiCls, mock_pdf, initial_state):
        """A TransactionPending record linked to the IngestJob."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        ingest_id = final_state["db_result"]["ingest_id"]

        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            txns = db.query(TransactionPending).filter(
                TransactionPending.ingest_id == ingest_id
            ).all()
            assert len(txns) >= 1

            # Status should be POSTED (updated by create_transaction_posted)
            assert txns[0].status == TransactionStatus.POSTED

            # Raw data should be stored
            assert txns[0].raw_data is not None
            assert txns[0].raw_data.get("nit_emisor") == "900123456"
        finally:
            db.close()

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_transaction_posted_created(self, MockGeminiCls, mock_pdf, initial_state):
        """A TransactionPosted with PUC classification and tax info."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        posted_id = final_state["db_result"]["transaction_posted_id"]

        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            posted = db.query(TransactionPosted).filter(
                TransactionPosted.id == posted_id
            ).first()
            assert posted is not None
            assert posted.cuenta_puc == "5110"  # Honorarios
            assert posted.retefuente == Decimal("150000")
            assert posted.reteica == Decimal("15000")
            assert posted.iva == Decimal("285000")
            assert posted.status == TransactionStatus.POSTED
        finally:
            db.close()

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_journal_entries_partida_doble(self, MockGeminiCls, mock_pdf, initial_state):
        """Journal entries must satisfy debits == credits (partida doble)."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        posted_id = final_state["db_result"]["transaction_posted_id"]

        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            lines = db.query(JournalEntryLine).filter(
                JournalEntryLine.transaction_posted_id == posted_id
            ).all()

            assert len(lines) >= 2, f"Expected at least 2 journal lines, got {len(lines)}"

            total_debito = sum(l.debito for l in lines)
            total_credito = sum(l.credito for l in lines)

            assert total_debito == total_credito, (
                f"Partida doble violated: debitos={total_debito} != creditos={total_credito}"
            )
            assert total_debito > 0  # Not all zeros
        finally:
            db.close()

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_audit_log_created(self, MockGeminiCls, mock_pdf, initial_state):
        """Audit log entries should exist for the ingest."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            logs = db.query(AuditLog).filter(
                AuditLog.action.like("%transaction%")
            ).order_by(AuditLog.created_at.desc()).limit(5).all()
            # At least one audit log from the pipeline
            assert len(logs) >= 1
        finally:
            db.close()


# ─── TEST: Error Propagation ────────────────────────────────────

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
        assert "not found" in final_state["error"].lower() or "File not found" in final_state["error"]
        assert final_state.get("db_result") is None  # DB persist skipped

    def test_non_pdf_file_error(self, tmp_path):
        """Non-PDF file → supervisor error."""
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
        assert "PDF" in final_state["error"] or "pdf" in final_state["error"]
        assert final_state.get("db_result") is None

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_gemini_exception_produces_error(self, MockGeminiCls, mock_pdf, initial_state):
        """If Gemini throws, ingest catches it and sets error."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = RuntimeError("Gemini API quota exceeded")
        MockGeminiCls.return_value = mock_instance

        graph = create_agent_graph()
        final_state = graph.invoke(initial_state)

        assert final_state.get("error") is not None
        assert "Gemini API quota" in final_state["error"] or "Ingest error" in final_state["error"]


# ─── TEST: invoke_agent wrapper ──────────────────────────────────

class TestInvokeAgent:
    """Test the invoke_agent() convenience function."""

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_invoke_agent_returns_result(self, MockGeminiCls, mock_pdf, tmp_path):
        """invoke_agent() should return a dict with status and validation_history."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        dummy_pdf = tmp_path / "test_invoice.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

        result = invoke_agent(str(dummy_pdf))

        assert isinstance(result, dict)
        assert result.get("status") == "completed"
        assert "validation_history" in result
        assert "db_result" in result

    @patch("app.agents.ingest_agent.extract_text_from_pdf", side_effect=_mock_extract_text)
    @patch("app.agents.ingest_agent.GeminiClient")
    def test_invoke_agent_includes_db_ids(self, MockGeminiCls, mock_pdf, tmp_path):
        """Result should include ingest_id and transaction_id from DB."""
        mock_instance = MagicMock()
        mock_instance.extract_receipt_data.side_effect = _mock_gemini_valid
        MockGeminiCls.return_value = mock_instance

        dummy_pdf = tmp_path / "invoice2.pdf"
        dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

        result = invoke_agent(str(dummy_pdf))

        assert result.get("db_persisted") is True
        assert result.get("ingest_id") is not None
        assert result.get("transaction_id") is not None


# ─── TEST: API Endpoint Integration ─────────────────────────────

class TestApiEndpoint:
    """Test the /api/v1/ingest/upload endpoint end-to-end."""

    @pytest.fixture(autouse=True)
    def _import_app(self):
        """Import main.app with root on sys.path."""
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)

    @patch("app.api.v1.ingest.invoke_agent")
    def test_upload_endpoint(self, mock_invoke):
        """POST /upload with a PDF should return IngestResponse."""
        from fastapi.testclient import TestClient
        from main import app  # noqa: E402

        mock_invoke.return_value = {
            "status": "completed",
            "message": "Receipt processed",
            "ingest_id": "ingest_test123",
            "process_id": "proc_123",
            "validation_history": [],
            "db_result": {"ingest_id": "ingest_test123"},
        }

        client = TestClient(app)

        # Create a minimal valid PDF
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 0\ntrailer\n<< >>\nstartxref\n0\n%%EOF"

        response = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("factura_test.pdf", pdf_content, "application/pdf")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "ingest_id" in data

    def test_upload_rejects_non_pdf(self):
        """Non-PDF files should be rejected with 400."""
        from fastapi.testclient import TestClient
        from main import app  # noqa: E402

        client = TestClient(app)
        response = client.post(
            "/api/v1/ingest/upload",
            files={"file": ("document.txt", b"hello world", "text/plain")},
        )

        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]
