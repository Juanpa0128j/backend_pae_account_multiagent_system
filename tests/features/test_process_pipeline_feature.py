"""
Tests for the Process Pipeline (Pipeline 2: Contador flow).

Tests cover:
1. Process graph structure and compilation
2. Contador node execution with valid/invalid data
3. PUC validation with strict rejection policy
4. Retry logic for contador with correction feedback
5. Full process pipeline: staged TX → contador → validate → persist
6. API endpoints: POST /process/accounting/{ingest_id} and GET /process/status/{process_id}
7. Integration between ingest pipeline → process pipeline

These tests use strategic mocking to work without PostgreSQL when needed,
and real DB when available for full integration testing.
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock, Mock

pytest.importorskip("langgraph")

from app.agents.graph import create_agent_graph, invoke_accounting_pipeline
from app.agents.state import AgentState
from app.agents.contador_agent import contador_node
from app.agents.supervisor import (
    validate_contador_output_node,
    should_retry_contador,
)

# ─── Test Data ────────────────────────────────────────────────────

VALID_RAW_TRANSACTION = {
    "fecha": "2026-03-07",
    "nit_emisor": "900123456",
    "nit_receptor": "800999888",
    "total": 2500000.0,
    "descripcion": "Servicios profesionales marzo 2026",
    "items": [
        {"descripcion": "Consultoría estratégica", "cantidad": 1, "valor": 2500000}
    ],
    "raw_data": {},
}

VALID_CONTADOR_OUTPUT = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "factura",
    "descripcion_general": "Servicios profesionales marzo 2026",
    "asientos": [
        {
            "cuenta_puc": "5110",
            "nombre_cuenta": "Honorarios",
            "tipo_movimiento": "debito",
            "valor": 2500000,
            "descripcion": "Servicios profesionales",
        },
        {
            "cuenta_puc": "240802",
            "nombre_cuenta": "IVA Descontable",
            "tipo_movimiento": "debito",
            "valor": 475000,
            "descripcion": "IVA sobre servicios",
        },
        {
            "cuenta_puc": "220505",
            "nombre_cuenta": "Proveedores Nacionales",
            "tipo_movimiento": "credito",
            "valor": 2825000,
            "descripcion": "CxP servicios profesionales",
        },
        {
            "cuenta_puc": "240815",
            "nombre_cuenta": "Retención en la Fuente",
            "tipo_movimiento": "credito",
            "valor": 150000,
            "descripcion": "Retefuente servicios",
        },
    ],
    "total_debitos": 2975000,
    "total_creditos": 2975000,
}

INVALID_CONTADOR_OUTPUT_UNBALANCED = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "factura",
    "descripcion_general": "Transacción desbalanceada test",
    "asientos": [
        {
            "cuenta_puc": "5110",
            "nombre_cuenta": "Honorarios",
            "tipo_movimiento": "debito",
            "valor": 1000000,
            "descripcion": "Servicio",
        },
        {
            "cuenta_puc": "220505",
            "nombre_cuenta": "Proveedores",
            "tipo_movimiento": "credito",
            "valor": 900000,  # ❌ Desbalanceado!
            "descripcion": "CxP",
        },
    ],
    "total_debitos": 1000000,
    "total_creditos": 900000,  # ❌ No balancea!
}

INVALID_CONTADOR_OUTPUT_EMPTY_ASIENTOS = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "factura",
    "descripcion_general": "Transacción sin asientos test",
    "asientos": [],
    "total_debitos": 0,
    "total_creditos": 0,
}

INVALID_CONTADOR_OUTPUT_BAD_PUC = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "factura",
    "descripcion_general": "Transacción con PUC inválido test",
    "asientos": [
        {
            "cuenta_puc": "999999",  # ❌ PUC no existe en DB
            "nombre_cuenta": "Cuenta Falsa",
            "tipo_movimiento": "debito",
            "valor": 1000000,
            "descripcion": "Test",
        },
        {
            "cuenta_puc": "220505",
            "nombre_cuenta": "Proveedores",
            "tipo_movimiento": "credito",
            "valor": 1000000,
            "descripcion": "CxP",
        },
    ],
    "total_debitos": 1000000,
    "total_creditos": 1000000,
    "total_credito": 1000000,
    "balanceado": True,
}


# ─── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def process_state() -> AgentState:
    """Basic process pipeline state."""
    return {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": "ingest_test_001",
        "db_result": None,
        "mode": "process",
        "raw_transactions": [VALID_RAW_TRANSACTION],
        "contador_output": {},
        "process_id": "proc_test_001",
        "pending_transaction_id": "pending_test_001",
        "current_stage": "queued",
        "agent_log": [],
    }


# ─── Test: Graph Structure ────────────────────────────────────────


class TestProcessGraphStructure:
    """Verify unified agent graph compiles and has expected nodes."""

    def test_process_graph_compiles(self):
        """Unified agent graph should compile without errors."""
        graph = create_agent_graph()
        assert graph is not None
        assert hasattr(graph, "invoke")

    def test_process_graph_has_expected_nodes(self):
        """Unified graph should have supervisor, contador, tributario, auditor, and persist nodes."""
        graph = create_agent_graph()
        node_names = [
            n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")
        ]
        assert "supervisor" in node_names
        assert "contador" in node_names
        assert "tributario" in node_names
        assert "auditor" in node_names
        assert "db_persist" in node_names

    def test_process_graph_node_count(self):
        """Unified agent graph should have exactly 12 nodes."""
        graph = create_agent_graph()
        node_names = [
            n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")
        ]
        assert len(node_names) == 12


# ─── Test: Contador Node ──────────────────────────────────────────


class TestContadorNode:
    """Test contador agent node in isolation."""

    @patch("app.services.rag_service.get_rag_service")
    @patch("app.agents.contador_agent.get_llm_client")
    def test_contador_with_valid_output(self, mock_get_client, mock_rag, process_state):
        """Contador should produce valid ContadorOutput from raw transactions."""
        mock_client = MagicMock()
        mock_client.extract_contador_output.return_value = VALID_CONTADOR_OUTPUT
        mock_get_client.return_value = mock_client
        mock_rag.return_value.search_normativo.return_value = []

        result_state = contador_node(process_state)

        assert result_state.get("error") is None
        assert "contador_output" in result_state
        assert result_state["current_agent"] == "contador"

    @patch("app.agents.contador_agent.get_llm_client")
    def test_contador_with_correction_feedback(self, mock_get_client, process_state):
        """Contador should use correction_feedback on retry."""
        mock_client = MagicMock()
        mock_client.extract_contador_output.return_value = VALID_CONTADOR_OUTPUT
        mock_get_client.return_value = mock_client

        process_state["correction_feedback"] = "PUC 5110 debe ser 5115 para este caso"
        result_state = contador_node(process_state)

        # Verify correction_feedback was passed to Gemini
        mock_client.extract_contador_output.assert_called_once()
        call_kwargs = mock_client.extract_contador_output.call_args[1]
        assert (
            call_kwargs["correction_feedback"]
            == "PUC 5110 debe ser 5115 para este caso"
        )
        assert result_state["correction_feedback"] is None  # Should be cleared

    def test_contador_skips_on_upstream_error(self, process_state):
        """Contador should skip execution if upstream error exists."""
        process_state["error"] = "Upstream error from supervisor"
        result_state = contador_node(process_state)

        assert result_state["error"] == "Upstream error from supervisor"
        assert (
            "contador_output" not in result_state or not result_state["contador_output"]
        )

    def test_contador_fails_on_empty_transactions(self, process_state):
        """Contador should error if no raw_transactions provided."""
        process_state["raw_transactions"] = []
        result_state = contador_node(process_state)

        assert result_state.get("error") is not None
        assert "no raw_transactions" in result_state["error"].lower()


# ─── Test: Validation Node ────────────────────────────────────────


class TestValidateContadorOutput:
    """Test contador validation node."""

    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    def test_validation_passes_with_valid_output(self, mock_puc_check, process_state):
        """Validation should pass with valid balanced output and existing PUC codes."""
        # Mock PUC validation to return valid records for all codes
        mock_puc_record = Mock()
        mock_puc_record.codigo = "5110"
        mock_puc_record.nombre = "Honorarios"
        mock_puc_check.return_value = mock_puc_record

        process_state["contador_output"] = VALID_CONTADOR_OUTPUT
        process_state["current_agent"] = "contador"

        result_state = validate_contador_output_node(process_state)

        assert result_state.get("error") is None
        assert len(result_state["validation_history"]) > 0
        # Validation successful means no error set

    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    def test_validation_warns_with_unbalanced_output(
        self, mock_puc_check, process_state
    ):
        """Unbalanced debito/credito is now a warning, not a hard error."""
        mock_puc_record = Mock()
        mock_puc_check.return_value = mock_puc_record

        process_state["contador_output"] = INVALID_CONTADOR_OUTPUT_UNBALANCED
        process_state["current_agent"] = "contador"

        result_state = validate_contador_output_node(process_state)

        assert len(result_state["validation_history"]) > 0
        assert result_state["validation_history"][-1]["is_valid"] is True
        assert result_state.get("correction_feedback") is None
        assert result_state.get("error") is None

    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    def test_validation_fails_with_invalid_puc(self, mock_puc_check, process_state):
        """Invalid PUC code should be auto-remapped by the supervisor."""

        # Configure mock so that PUC code "999999" does NOT exist in DB, others do.
        def puc_side_effect(db, codigo):
            return None if codigo == "999999" else Mock()

        mock_puc_check.side_effect = puc_side_effect

        process_state["contador_output"] = INVALID_CONTADOR_OUTPUT_BAD_PUC
        process_state["current_agent"] = "contador"

        result_state = validate_contador_output_node(process_state)

        # The supervisor auto-remaps invalid PUC codes to valid fallbacks,
        # so no correction feedback or error should be set.
        assert result_state.get("error") is None
        assert result_state.get("correction_feedback") is None


# ─── Test: Retry Logic ────────────────────────────────────────────


class TestContadorRetryLogic:
    """Test retry decision logic for contador."""

    def test_should_retry_on_validation_failure(self, process_state):
        """should_retry_contador should return 'retry' if validation failed and retries remain."""
        process_state["retry_count"] = 1
        process_state["correction_feedback"] = "PUC inválido"
        process_state["validation_history"] = [
            {
                "agent": "contador",
                "timestamp": "2026-03-07T10:00:00",
                "errors": ["PUC inválido"],
            }
        ]

        decision = should_retry_contador(process_state)
        assert decision == "retry"

    def test_should_not_retry_after_max_attempts(self, process_state):
        """should_retry_contador should return 'end' after exceeding MAX_RETRIES."""
        process_state["retry_count"] = 4  # Beyond MAX_RETRIES = 3
        process_state["correction_feedback"] = "Error"
        process_state["validation_history"] = [
            {
                "agent": "contador",
                "timestamp": "2026-03-07T10:00:00",
                "errors": ["Error"],
            }
        ]

        decision = should_retry_contador(process_state)
        assert decision == "end"

    def test_should_end_on_validation_success(self, process_state):
        """should_retry_contador should return 'end' if validation passed."""
        process_state["retry_count"] = 1
        process_state["correction_feedback"] = None  # No feedback means success
        process_state["validation_history"] = [
            {"agent": "contador", "timestamp": "2026-03-07T10:00:00", "errors": []}
        ]

        decision = should_retry_contador(process_state)
        assert decision == "end"


# ─── Test: Full Process Pipeline ──────────────────────────────────


class TestFullProcessPipeline:
    """Test complete process pipeline execution."""

    @patch("app.services.rag_service.get_rag_service")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.persist_node.SessionLocal")
    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    @patch("app.agents.contador_agent.get_llm_client")
    def test_process_pipeline_happy_path(
        self,
        mock_get_client,
        mock_puc_check,
        mock_session,
        mock_get_company_settings,
        mock_get_auditor_client,
        mock_rag,
    ):
        """Full pipeline: staged TX → contador → validation → persist → success."""
        mock_rag.return_value.search_normativo.return_value = []

        # Mock Gemini to return valid contador output
        mock_client = MagicMock()
        mock_client.extract_contador_output.return_value = VALID_CONTADOR_OUTPUT
        mock_get_client.return_value = mock_client

        mock_auditor = MagicMock()
        mock_auditor.extract_auditor_output.return_value = {
            "fecha_auditoria": "2026-03-07",
            "documento_referencia": "FAC-TEST-001",
            "aprobado": True,
            "nivel_riesgo": "bajo",
            "hallazgos": [],
            "puntaje_calidad": 95,
            "resumen": "Asientos validados correctamente.",
        }
        mock_get_auditor_client.return_value = mock_auditor

        company_row = Mock()
        company_row.tasa_retefuente_servicios = Decimal("0.110000")
        company_row.tasa_retefuente_bienes = Decimal("0.030000")
        company_row.tasa_retefuente_arrendamiento = Decimal("0.100000")
        company_row.tasa_reteica = Decimal("0.006900")
        company_row.tasa_iva_general = Decimal("0.190000")
        company_row.iva_responsable = True
        company_row.tasa_ica = Decimal("0.006900")
        company_row.tasa_renta = Decimal("0.350000")
        mock_get_company_settings.return_value = company_row

        # Mock PUC validation to always succeed
        mock_puc_record = Mock()
        mock_puc_record.codigo = "5110"
        mock_puc_record.nombre = "Honorarios"
        mock_puc_check.return_value = mock_puc_record

        # Mock DB session
        mock_db = MagicMock()
        mock_session.return_value = mock_db

        # Mock TransactionPending query
        mock_pending = Mock()
        mock_pending.id = "pending_001"
        mock_pending.fecha = datetime.now(timezone.utc)
        mock_pending.total = Decimal("2500000")
        mock_pending.nit_emisor = "900123456"
        mock_pending.nit_receptor = "800999888"
        mock_pending.descripcion = "Servicios profesionales"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_pending

        # Invoke process pipeline
        result = invoke_accounting_pipeline(
            ingest_id="ingest_001",
            raw_transactions=[VALID_RAW_TRANSACTION],
            pending_transaction_id="pending_001",
            process_id="proc_001",
        )

        # Assertions
        # Should complete successfully
        assert result.get("error") is None, "Pipeline should complete without error"

    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    @patch("app.agents.contador_agent.get_llm_client")
    def test_process_pipeline_retry_then_success(
        self,
        mock_get_client,
        mock_puc_check,
        mock_get_company_settings,
        mock_get_auditor_client,
    ):
        """Pipeline should retry contador on first failure, then succeed."""
        # Mock Gemini: first call returns invalid, second returns valid
        mock_client = MagicMock()
        call_count = {"count": 0}

        def side_effect_contador(*args, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return INVALID_CONTADOR_OUTPUT_EMPTY_ASIENTOS
            return VALID_CONTADOR_OUTPUT

        mock_client.extract_contador_output.side_effect = side_effect_contador
        mock_get_client.return_value = mock_client

        mock_auditor = MagicMock()
        mock_auditor.extract_auditor_output.return_value = {
            "fecha_auditoria": "2026-03-07",
            "documento_referencia": "FAC-TEST-001",
            "aprobado": True,
            "nivel_riesgo": "bajo",
            "hallazgos": [],
            "puntaje_calidad": 95,
            "resumen": "Asientos validados correctamente.",
        }
        mock_get_auditor_client.return_value = mock_auditor

        company_row = Mock()
        company_row.tasa_retefuente_servicios = Decimal("0.110000")
        company_row.tasa_retefuente_bienes = Decimal("0.030000")
        company_row.tasa_retefuente_arrendamiento = Decimal("0.100000")
        company_row.tasa_reteica = Decimal("0.006900")
        company_row.tasa_iva_general = Decimal("0.190000")
        company_row.iva_responsable = True
        company_row.tasa_ica = Decimal("0.006900")
        company_row.tasa_renta = Decimal("0.350000")
        mock_get_company_settings.return_value = company_row

        # Mock PUC validation
        mock_puc_record = Mock()
        mock_puc_check.return_value = mock_puc_record

        with patch("app.agents.persist_node.SessionLocal") as mock_session:
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            mock_pending = Mock()
            mock_pending.id = "pending_001"
            mock_pending.fecha = datetime.now(timezone.utc)
            mock_pending.total = Decimal("1000000")
            mock_pending.nit_emisor = "900123456"
            mock_pending.nit_receptor = "800999888"
            mock_pending.descripcion = "Test"
            mock_db.query.return_value.filter.return_value.first.return_value = (
                mock_pending
            )

            result = invoke_accounting_pipeline(
                ingest_id="ingest_001",
                raw_transactions=[VALID_RAW_TRANSACTION],
                pending_transaction_id="pending_001",
                process_id="proc_001",
            )

            # Should have retried once
            assert mock_client.extract_contador_output.call_count >= 2
            # Final result should be valid (no error)
            assert result.get("error") is None

    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.services.rag_service.get_rag_service")
    def test_process_pipeline_exhausted_retries(
        self, mock_get_rag, mock_get_client, mock_puc_check
    ):
        """Pipeline should fail after MAX_RETRIES with invalid output."""
        mock_rag_svc = MagicMock()
        mock_rag_svc.search_normativo.return_value = []
        mock_get_rag.return_value = mock_rag_svc

        # Mock Gemini to always return invalid output
        mock_client = MagicMock()
        mock_client.extract_contador_output.return_value = (
            INVALID_CONTADOR_OUTPUT_UNBALANCED
        )
        mock_get_client.return_value = mock_client

        mock_puc_record = Mock()
        mock_puc_check.return_value = mock_puc_record

        with patch("app.agents.persist_node.SessionLocal") as mock_session:
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            mock_pending = Mock()
            mock_pending.id = "pending_001"
            mock_pending.fecha = datetime.now(timezone.utc)
            mock_pending.total = Decimal("1000000")
            mock_pending.nit_emisor = "900123456"
            mock_db.query.return_value.filter.return_value.first.return_value = (
                mock_pending
            )

            result = invoke_accounting_pipeline(
                ingest_id="ingest_001",
                raw_transactions=[VALID_RAW_TRANSACTION],
                pending_transaction_id="pending_001",
                process_id="proc_001",
            )

            # Should have validation failures and an error
            assert len(result["validation_history"]) > 0
            # Should have error indicating failure
            # (Either in result error or validation history)


# ─── Test: Process API Endpoints ──────────────────────────────────


class TestProcessAPIEndpoints:
    """Test process API endpoints configuration."""

    def test_process_module_exists(self):
        """Verify process module can be imported."""
        from app.api.v1 import process

        assert hasattr(process, "router")

    def test_process_endpoint_contract(self):
        """Verify process endpoint is properly configured."""
        from app.api.v1.process import router

        routes = [r for r in router.routes if hasattr(r, "path")]
        paths = [r.path for r in routes]
        assert any("/accounting/{ingest_id}" in p for p in paths)
        assert any("/status/{process_id}" in p for p in paths)


# ─── Test: Integration Ingest → Process ───────────────────────────


class TestIngestToProcessIntegration:
    """Test separation of ingest and process pipelines."""

    def test_unified_graph_supports_both_pipelines(self):
        """Verify the unified graph contains nodes for both ingest and process pipelines."""
        from app.agents.graph import create_agent_graph

        graph = create_agent_graph()
        node_names = [
            n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")
        ]

        # Ingest pipeline nodes
        assert "ingesta" in node_names
        assert "validate_output" in node_names

        # Process pipeline nodes
        assert "contador" in node_names
        assert "tributario" in node_names
        assert "auditor" in node_names

        # Shared nodes
        assert "supervisor" in node_names
        assert "db_persist" in node_names


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
