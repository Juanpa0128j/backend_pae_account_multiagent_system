"""
E2E Phase 2 — Supervisor Funcional & Testing.

Tests the full routing FSM across all pipelines with all external
dependencies mocked. Validates:
  - Graph structure: 9 nodes present (including error_terminal)
  - Pipeline 1 (ingest): supervisor → ingesta → validate → db_persist
  - Supervisor error routing: file errors → error_terminal (not ingesta)
  - Pipeline 2 (process): supervisor routes to contador → tributario → auditor
  - Retry cycle: invalid output → correction_feedback → retry → success
  - Exhausted retries: 3 invalid → validation_exhausted event, error set
  - agent_log: populated with standardised entries at each node
  - invoke_ingest_pipeline: returns agent_log and validation_history in result dict
"""

import pytest
from unittest.mock import MagicMock, patch

from app.models.llm_schemas import TaxJustification

# ---------------------------------------------------------------------------
# Mock targets — must match the import location in each module
# ---------------------------------------------------------------------------
MOCK_LLAMA_PARSE = "app.agents.ingest_agent.LlamaParse"
MOCK_GEMINI = "app.agents.ingest_agent.get_llm_client"
MOCK_SESSION = "app.agents.persist_node.SessionLocal"
MOCK_DB_SVC = "app.agents.persist_node.db_service"
MOCK_TRIBUTARIO_GEMINI = "app.agents.tributario_agent.get_llm_client"
MOCK_TRIBUTARIO_RAG = "app.agents.tributario_agent.get_rag_service"
MOCK_TRIBUTARIO_SESSION = "app.core.database.SessionLocal"
MOCK_TRIBUTARIO_DB_SVC = "app.services.db_service.get_company_settings"
MOCK_AUDITOR_GEMINI = "app.agents.auditor_agent.get_llm_client"

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------
VALID_DATA = {
    "fecha": "2026-01-15",
    "monto": 1500000.0,
    "concepto": "Consultoría contable enero 2026",
    "beneficiario": "Contadores SAS",
    "empresa": "Mi Empresa SAS",
    "referencia": "FAC-2026-001",
    "tipo_documento": "factura",
    "transactions": [
        {
            "fecha": "2026-01-15",
            "nit_emisor": "900123456",
            "nit_receptor": "800999888",
            "total": 1500000.0,
            "descripcion": "Consultoría contable enero 2026",
            "items": [{"descripcion": "Consultoría", "cantidad": 1, "valor": 1500000}],
        }
    ],
}

INVALID_DATA = {
    "transactions": [
        {
            "fecha": "bad-date",
            "nit_emisor": "",
            "nit_receptor": "",
            "total": -100,
        }
    ]
}

SAMPLE_TEXT = "FACTURA No. FV-2026-001\nNIT: 900.123.456-7\nTotal: $1.500.000"


VALID_CONTADOR_OUTPUT = {
    "fecha_registro": "2026-01-15",
    "tipo_documento": "factura",
    "descripcion_general": "Servicios enero 2026",
    "asientos": [
        {
            "cuenta_puc": "5110",
            "nombre_cuenta": "Honorarios",
            "valor": 1500000.0,
            "debito": 1500000.0,
            "credito": 0.0,
            "tipo_movimiento": "debito",
        },
        {
            "cuenta_puc": "2408",
            "nombre_cuenta": "Retefuente",
            "valor": 150000.0,
            "debito": 0.0,
            "credito": 150000.0,
            "tipo_movimiento": "credito",
        },
        {
            "cuenta_puc": "2205",
            "nombre_cuenta": "Proveedores",
            "valor": 1350000.0,
            "debito": 0.0,
            "credito": 1350000.0,
            "tipo_movimiento": "credito",
        },
    ],
    "total_debitos": 1500000.0,
    "total_creditos": 1500000.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_tributario_mocks(mock_trib_rag_cls, mock_trib_gemini_fn):
    """Wire up mock RAG and Gemini for tributario node."""
    mock_rag = MagicMock()
    mock_rag.search_normativo.return_value = []
    mock_trib_rag_cls.return_value = mock_rag

    mock_gc = MagicMock()
    mock_gc.justify_tax_analysis.return_value = TaxJustification(
        referencias=["Art. 383 ET", "Art. 477 ET"],
        justificacion="Retenciones aplicadas según ET vigente.",
        confirma_tasas=True,
    )
    mock_trib_gemini_fn.return_value = mock_gc


def _setup_auditor_mock(mock_auditor_gemini_fn):
    """Provide a deterministic auditor output that satisfies required schema."""
    mock_gc = MagicMock()
    mock_gc.extract_auditor_output.return_value = {
        "fecha_auditoria": "2026-01-16",
        "documento_referencia": "Transaccion_2026-01-15_NIT_900123456",
        "aprobado": True,
        "nivel_riesgo": "bajo",
        "hallazgos": [],
        "puntaje_calidad": 95,
        "resumen": "Auditoria aprobada sin hallazgos materiales.",
    }
    mock_auditor_gemini_fn.return_value = mock_gc


def _mock_llama(text: str = SAMPLE_TEXT):
    doc = MagicMock()
    doc.text = text
    parser = MagicMock()
    parser.load_data.return_value = [doc]
    return MagicMock(return_value=parser)


def _mock_gemini(data: dict):
    client = MagicMock()
    client.extract_transactions.return_value = data
    return MagicMock(return_value=client)


def _setup_db(mock_session_cls, mock_db_svc):
    """Wire up mock DB service so db_persist_node runs without a real DB."""
    db = MagicMock()
    mock_session_cls.return_value = db
    job = MagicMock()
    job.id = "mock-ingest-id"
    for method in ["create_ingest_job", "get_ingest_job", "update_ingest_job"]:
        getattr(mock_db_svc, method).return_value = job
    pending = MagicMock()
    pending.id = "mock-pending-id"
    mock_db_svc.create_transaction_pending.return_value = pending
    mock_db_svc.check_duplicates.return_value = []
    mock_db_svc.validate_puc_exists.return_value = None
    posted = MagicMock()
    posted.id = "mock-posted-id"
    mock_db_svc.create_transaction_posted.return_value = posted
    mock_db_svc.create_journal_entry_lines.return_value = [MagicMock()]


def _base_state(file_path: str = "", mode: str = "ingest") -> dict:
    return {
        "file_path": file_path,
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
        "mode": mode,
        "raw_transactions": [],
        "contador_output": {},
        "tributario_output": {},
        "company_config": None,
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": None,
        "agent_log": [],
        "audit_decision": None,
        "audit_feedback": None,
    }


@pytest.fixture
def dummy_pdf(tmp_path) -> str:
    p = tmp_path / "factura_test.pdf"
    p.write_bytes(b"%PDF-1.4 dummy")
    return str(p)


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------


class TestGraphStructure:
    def test_all_nine_nodes_exist(self):
        from app.agents.graph import create_agent_graph

        nodes = {
            n
            for n in create_agent_graph().get_graph().nodes
            if n not in ("__start__", "__end__")
        }
        expected = {
            "supervisor",
            "ingesta",
            "validate_output",
            "db_persist",
            "error_terminal",
            "contador",
            "tributario",
            "auditor",
            "reportero",
            "import_existing",
        }
        assert expected == nodes

    def test_error_terminal_node_present(self):
        from app.agents.graph import create_agent_graph

        nodes = {
            n
            for n in create_agent_graph().get_graph().nodes
            if n not in ("__start__", "__end__")
        }
        assert "error_terminal" in nodes


# ---------------------------------------------------------------------------
# Pipeline 1 happy path
# ---------------------------------------------------------------------------


class TestPipeline1HappyPath:
    @patch(MOCK_DB_SVC)
    @patch(MOCK_SESSION)
    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_full_ingesta_pipeline_no_error(
        self, mock_llama, mock_gemini, mock_session, mock_db_svc, dummy_pdf
    ):
        _setup_db(mock_session, mock_db_svc)
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(VALID_DATA).return_value

        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state(dummy_pdf))
        assert fs.get("error") is None
        assert fs["result"].get("status") == "completed"

    @patch(MOCK_DB_SVC)
    @patch(MOCK_SESSION)
    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_agent_log_contains_routing_and_validation(
        self, mock_llama, mock_gemini, mock_session, mock_db_svc, dummy_pdf
    ):
        _setup_db(mock_session, mock_db_svc)
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(VALID_DATA).return_value

        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state(dummy_pdf))
        events = [e["event"] for e in fs.get("agent_log", [])]
        assert "routing_start" in events
        assert "routing_complete" in events
        assert "validation_success" in events

    @patch(MOCK_DB_SVC)
    @patch(MOCK_SESSION)
    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_agent_log_entry_schema(
        self, mock_llama, mock_gemini, mock_session, mock_db_svc, dummy_pdf
    ):
        _setup_db(mock_session, mock_db_svc)
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(VALID_DATA).return_value

        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state(dummy_pdf))
        for entry in fs.get("agent_log", []):
            assert {"timestamp", "agent", "event", "details"} <= entry.keys()
            assert isinstance(entry["details"], dict)


# ---------------------------------------------------------------------------
# Supervisor error routing
# ---------------------------------------------------------------------------


class TestSupervisorErrorRouting:
    def test_missing_file_sets_error_and_goes_to_error_terminal(self):
        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state("/nonexistent/fake.pdf"))
        assert fs.get("error") is not None
        assert "not found" in fs["error"].lower()
        assert fs["result"].get("status") == "error"

    def test_missing_file_does_not_call_session_local(self):
        from app.agents.graph import create_agent_graph

        with patch(MOCK_SESSION) as mock_session:
            create_agent_graph().invoke(_base_state("/nonexistent/fake.pdf"))
            mock_session.assert_not_called()

    def test_non_pdf_sets_error_and_goes_to_error_terminal(self, tmp_path):
        txt = tmp_path / "doc.txt"
        txt.write_text("not a pdf")
        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state(str(txt)))
        assert fs.get("error") is not None
        assert fs["result"].get("status") == "error"

    def test_routing_error_event_in_agent_log(self):
        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state("/nonexistent/fake.pdf"))
        events = [e["event"] for e in fs.get("agent_log", [])]
        assert "routing_error" in events

    def test_pipeline_aborted_event_in_agent_log(self):
        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state("/nonexistent/fake.pdf"))
        events = [e["event"] for e in fs.get("agent_log", [])]
        assert "pipeline_aborted" in events


# ---------------------------------------------------------------------------
# Pipeline 2 — accounting routing with stubs
# ---------------------------------------------------------------------------


class TestPipeline2Routing:
    def test_accounting_pipeline_visits_contador_tributario_auditor(self, dummy_pdf):
        """Stub loop: supervisor → contador → supervisor → tributario → supervisor
        → auditor → supervisor → db_persist."""
        from app.agents.graph import create_agent_graph

        with (
            patch(MOCK_SESSION),
            patch(MOCK_DB_SVC),
            patch(MOCK_TRIBUTARIO_SESSION),
            patch(MOCK_TRIBUTARIO_DB_SVC) as mock_get_settings,
            patch("app.agents.supervisor.db_service.validate_puc_exists") as mock_puc,
            patch(MOCK_TRIBUTARIO_RAG) as mock_trib_rag,
            patch(MOCK_TRIBUTARIO_GEMINI) as mock_trib_gemini,
            patch(MOCK_AUDITOR_GEMINI) as mock_auditor_gemini,
        ):
            mock_get_settings.return_value = MagicMock(
                tasa_retefuente_servicios=0.11,
                tasa_retefuente_bienes=0.03,
                tasa_retefuente_arrendamiento=0.035,
                tasa_reteica=0.0048,
                tasa_iva_general=0.19,
                tasa_ica=0.0048,
                tasa_renta=0.33,
            )
            mock_puc.return_value = MagicMock(codigo="5110", nombre="Honorarios")
            _setup_tributario_mocks(mock_trib_rag, mock_trib_gemini)
            _setup_auditor_mock(mock_auditor_gemini)
            # Provide raw_transactions so process supervisor doesn't error
            s = _base_state(dummy_pdf, mode="process")
            s["raw_transactions"] = [
                {
                    "fecha": "2026-01-15",
                    "nit_emisor": "900123456",
                    "nit_receptor": "800999888",
                    "total": 500000,
                    "descripcion": "Servicio",
                }
            ]
            # Stub the Gemini client used by contador (not relevant for routing test)
            with patch("app.agents.contador_agent.get_llm_client") as mock_gc:
                mock_gc.return_value.extract_contador_output.return_value = (
                    VALID_CONTADOR_OUTPUT
                )
                fs = create_agent_graph().invoke(s)

            agents_visited = {e["agent"] for e in fs.get("agent_log", [])}
            assert "contador" in agents_visited
            assert "tributario" in agents_visited
            assert "auditor" in agents_visited

    def test_accounting_pipeline_auditor_approves_then_routes_to_db_persist(
        self, dummy_pdf
    ):
        """After auditor auto-approves (stub), supervisor sets current_agent=db_persist."""
        from app.agents.graph import create_agent_graph

        s = _base_state(dummy_pdf, mode="process")
        s["raw_transactions"] = [
            {
                "fecha": "2026-01-15",
                "nit_emisor": "900123456",
                "nit_receptor": "800999888",
                "total": 500000,
            }
        ]
        with (
            patch("app.agents.contador_agent.get_llm_client") as mock_gc,
            patch(MOCK_SESSION),
            patch(MOCK_DB_SVC),
            patch(MOCK_TRIBUTARIO_SESSION),
            patch(MOCK_TRIBUTARIO_DB_SVC) as mock_get_settings,
            patch("app.agents.supervisor.db_service.validate_puc_exists") as mock_puc,
            patch(MOCK_TRIBUTARIO_RAG) as mock_trib_rag,
            patch(MOCK_TRIBUTARIO_GEMINI) as mock_trib_gemini,
            patch(MOCK_AUDITOR_GEMINI) as mock_auditor_gemini,
        ):
            mock_gc.return_value.extract_contador_output.return_value = (
                VALID_CONTADOR_OUTPUT
            )
            mock_get_settings.return_value = MagicMock(
                tasa_retefuente_servicios=0.11,
                tasa_retefuente_bienes=0.03,
                tasa_retefuente_arrendamiento=0.035,
                tasa_reteica=0.0048,
                tasa_iva_general=0.19,
                tasa_ica=0.0048,
                tasa_renta=0.33,
            )
            mock_puc.return_value = MagicMock(codigo="5110", nombre="Honorarios")
            _setup_tributario_mocks(mock_trib_rag, mock_trib_gemini)
            _setup_auditor_mock(mock_auditor_gemini)
            fs = create_agent_graph().invoke(s)

        # Supervisor should have logged routing to db_persist after audit approved
        routing_completions = [
            e
            for e in fs.get("agent_log", [])
            if e["agent"] == "supervisor" and e["event"] == "routing_complete"
        ]
        next_agents = [e["details"].get("next_agent") for e in routing_completions]
        assert "db_persist" in next_agents


# ---------------------------------------------------------------------------
# Retry flow (Pipeline 1)
# ---------------------------------------------------------------------------


class TestRetryFlow:
    @patch(MOCK_DB_SVC)
    @patch(MOCK_SESSION)
    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_retry_once_then_success(
        self, mock_llama, mock_gemini, mock_session, mock_db_svc, dummy_pdf
    ):
        """First Gemini call returns invalid, second returns valid → retry succeeds."""
        _setup_db(mock_session, mock_db_svc)
        mock_llama.return_value = _mock_llama().return_value

        call_count = {"n": 0}

        def side_effect(text, correction_feedback=None):
            call_count["n"] += 1
            return INVALID_DATA if call_count["n"] == 1 else VALID_DATA

        client = MagicMock()
        client.extract_transactions.side_effect = side_effect
        mock_gemini.return_value = client

        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state(dummy_pdf))

        assert fs.get("error") is None
        history = fs.get("validation_history", [])
        assert len(history) >= 2
        assert history[0]["is_valid"] is False
        assert history[-1]["is_valid"] is True
        events = [e["event"] for e in fs.get("agent_log", [])]
        assert "validation_failure" in events
        assert "validation_success" in events

    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_exhausted_retries_sets_error(self, mock_llama, mock_gemini, dummy_pdf):
        """3 invalid outputs → error set, validation_exhausted in agent_log."""
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(INVALID_DATA).return_value

        from app.agents.graph import create_agent_graph

        fs = create_agent_graph().invoke(_base_state(dummy_pdf))
        assert fs.get("error") is not None
        events = [e["event"] for e in fs.get("agent_log", [])]
        assert "validation_exhausted" in events

    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_exhausted_retries_does_not_call_db(
        self, mock_llama, mock_gemini, dummy_pdf
    ):
        """On error path, should_retry_agent returns 'error' → graph goes to END."""
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(INVALID_DATA).return_value

        from app.agents.graph import create_agent_graph

        with patch(MOCK_SESSION) as mock_session:
            create_agent_graph().invoke(_base_state(dummy_pdf))
            mock_session.assert_not_called()


# ---------------------------------------------------------------------------
# invoke_ingest_pipeline wrapper
# ---------------------------------------------------------------------------


class TestInvokeAgent:
    @patch(MOCK_DB_SVC)
    @patch(MOCK_SESSION)
    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_result_includes_agent_log(
        self, mock_llama, mock_gemini, mock_session, mock_db_svc, dummy_pdf
    ):
        _setup_db(mock_session, mock_db_svc)
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(VALID_DATA).return_value

        from app.agents.graph import invoke_ingest_pipeline

        result = invoke_ingest_pipeline(dummy_pdf)
        assert "agent_log" in result
        assert isinstance(result["agent_log"], list)
        assert len(result["agent_log"]) > 0

    @patch(MOCK_DB_SVC)
    @patch(MOCK_SESSION)
    @patch(MOCK_GEMINI)
    @patch(MOCK_LLAMA_PARSE)
    def test_result_includes_validation_history(
        self, mock_llama, mock_gemini, mock_session, mock_db_svc, dummy_pdf
    ):
        _setup_db(mock_session, mock_db_svc)
        mock_llama.return_value = _mock_llama().return_value
        mock_gemini.return_value = _mock_gemini(VALID_DATA).return_value

        from app.agents.graph import invoke_ingest_pipeline

        result = invoke_ingest_pipeline(dummy_pdf)
        assert "validation_history" in result
        assert len(result["validation_history"]) >= 1
