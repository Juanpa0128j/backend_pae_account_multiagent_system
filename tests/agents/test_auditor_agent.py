"""
Tests for the Auditor Agent, Contador Agent, and the Process Graph pipeline.

Coverage:
  1. Unit: auditor_node — success, rejection, retry behaviour, error propagation
  2. Unit: contador_node — success, RAG failure graceful, retry, error propagation
  3. Unit: process_supervisor_node — validation and state initialisation
  4. Unit: validate_auditor_output_node — approval, rejection, schema-retry, history
  5. Unit: validate_contador_output_node — valid, PUC missing, schema-retry, exhausted
  6. Unit: should_retry_contador / should_retry_auditor — conditional edge routing
  7. Process graph structure — node set, edge count, entry point
  8. E2E (no DB): full process graph with all DB calls mocked
  9. E2E (no DB): audit rejection does NOT block DB persist (risk stored, not hard-blocked)
 10. E2E (no DB): contador retry on invalid schema, eventual success
 11. E2E (no DB): auditor retry on invalid schema, eventual success
 12. DB integration: full pipeline persists TransactionPosted + JournalEntryLines
     (requires PostgreSQL — skipped if unavailable)
"""

import os
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agents.auditor_agent import auditor_node
from app.agents.contador_agent import contador_node
from app.agents.graph import create_agent_graph, invoke_accounting_pipeline
from app.agents.state import AgentState
from app.agents.supervisor import (
    _normalize_contador_puc_codes,
    process_supervisor_node,
    supervisor_node,
    should_retry_auditor,
    should_retry_contador,
    validate_auditor_output_node,
    validate_contador_output_node,
)
from app.models.document_types import DocumentType, IngestPathway

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pae_user:password@localhost:5432/pae_accounting",
)

VALID_RAW_TRANSACTIONS: list[dict] = [
    {
        "fecha": "2026-01-15",
        "nit_emisor": "900123456",
        "nit_receptor": "800999888",
        "total": 1_500_000,
        "descripcion": "Servicio de consultoría contable mensual",
        "items": [],
    }
]

VALID_CONTADOR_OUTPUT: dict = {
    "fecha_registro": "2026-01-15",
    "tipo_documento": "factura",
    "descripcion_general": "Registro factura servicio consultoría contable",
    "asientos": [
        {
            "cuenta_puc": "5135",
            "nombre_cuenta": "Servicios",
            "tipo_movimiento": "debito",
            "valor": 1_500_000,
            "descripcion": "Gasto consultoría contable",
        },
        {
            "cuenta_puc": "2205",
            "nombre_cuenta": "Proveedores nacionales",
            "tipo_movimiento": "credito",
            "valor": 1_500_000,
            "descripcion": "Obligación con ContaExpress SAS",
        },
    ],
    "total_debitos": 1_500_000,
    "total_creditos": 1_500_000,
}

# Invalid contador output: debits ≠ credits (now a warning, not a hard error)
INVALID_CONTADOR_OUTPUT: dict = {
    **VALID_CONTADOR_OUTPUT,
    "total_creditos": 999_999,
    "asientos": [
        VALID_CONTADOR_OUTPUT["asientos"][0],
        {**VALID_CONTADOR_OUTPUT["asientos"][1], "valor": 999_999},
    ],
}

# Hard invalid contador output: empty asientos (still a hard validation error)
INVALID_CONTADOR_OUTPUT_HARD: dict = {
    **VALID_CONTADOR_OUTPUT,
    "asientos": [],
}

VALID_AUDITOR_OUTPUT: dict = {
    "fecha_auditoria": "2026-01-16",
    "documento_referencia": "FAC-2026-001",
    "aprobado": True,
    "nivel_riesgo": "bajo",
    "hallazgos": [],
    "puntaje_calidad": 95.0,
    "resumen": "Asientos contables correctos y coherentes con la normativa colombiana.",
}

REJECTED_AUDITOR_OUTPUT: dict = {
    "fecha_auditoria": "2026-01-16",
    "documento_referencia": "FAC-2026-001",
    "aprobado": False,
    "nivel_riesgo": "alto",
    "hallazgos": [
        {
            "codigo": "AUD-001",
            "severidad": "error",
            "descripcion": "Monto registrado no coincide con el documento fuente original",
            "campo_afectado": "total",
            "recomendacion": "Verificar el monto contra la factura original",
        }
    ],
    "puntaje_calidad": 35.0,
    "resumen": "Asientos rechazados por inconsistencia en montos registrados.",
}

# Schematically invalid auditor output (missing required fields)
INVALID_AUDITOR_OUTPUT: dict = {
    "aprobado": None,  # must be bool
    "nivel_riesgo": "bajo",
    # missing: fecha_auditoria, documento_referencia, puntaje_calidad, resumen
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_state(**overrides: Any) -> AgentState:
    """Build a minimal AgentState for process-pipeline unit tests."""
    state: AgentState = {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": "test-ingest-001",
        "db_result": None,
        "mode": "process",
        "raw_transactions": list(VALID_RAW_TRANSACTIONS),
        "contador_output": {},
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": None,
        "agent_log": [],
        "auditor_output": {},
        "audit_approved": None,
        "audit_rejection_reason": None,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


# ===========================================================================
# 1. Unit: auditor_node
# ===========================================================================


class TestAuditorNode:
    def test_skips_on_upstream_error(self):
        """auditor_node should be a no-op when state["error"] is set."""
        state = _base_state(
            error="upstream failure", contador_output=dict(VALID_CONTADOR_OUTPUT)
        )
        result = auditor_node(state)
        assert result["error"] == "upstream failure"
        assert result["auditor_output"] == {}

    def test_fails_without_contador_output(self):
        """Missing contador_output must set an error, not raise an exception."""
        state = _base_state(contador_output={})
        result = auditor_node(state)
        assert result["error"] is not None
        assert "contador" in result["error"].lower()

    @patch("app.agents.auditor_agent.get_llm_client")
    def test_successful_approved_audit(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_factory.return_value = mock_llm

        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT))
        result = auditor_node(state)

        assert result["error"] is None
        assert result["auditor_output"]["aprobado"] is True
        assert result["auditor_output"]["nivel_riesgo"] == "bajo"
        assert result["current_agent"] == "auditor"
        assert result["current_stage"] == "auditor"
        assert result["result"]["audit_approved"] is True

    @patch("app.agents.auditor_agent.get_llm_client")
    def test_rejected_audit_stored_in_state(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.extract_auditor_output.return_value = dict(REJECTED_AUDITOR_OUTPUT)
        mock_factory.return_value = mock_llm

        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT))
        result = auditor_node(state)

        assert result["error"] is None
        assert result["auditor_output"]["aprobado"] is False
        assert any(
            h["codigo"] == "AUD-001" for h in result["auditor_output"]["hallazgos"]
        )

    @patch("app.agents.auditor_agent.get_llm_client")
    def test_retry_clears_correction_feedback(self, mock_factory):
        """After a successful call, correction_feedback must be cleared."""
        mock_llm = MagicMock()
        mock_llm.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_factory.return_value = mock_llm

        state = _base_state(
            contador_output=dict(VALID_CONTADOR_OUTPUT),
            correction_feedback="Corrige el campo resumen.",
            retry_count=1,
        )
        result = auditor_node(state)

        assert result["correction_feedback"] is None
        assert result["error"] is None

    @patch("app.agents.auditor_agent.get_llm_client")
    def test_retry_passes_feedback_to_gemini(self, mock_factory):
        """On retry, correction_feedback must be forwarded to Gemini."""
        mock_llm = MagicMock()
        mock_llm.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_factory.return_value = mock_llm

        feedback = "Corrige el formato del campo nivel_riesgo."
        state = _base_state(
            contador_output=dict(VALID_CONTADOR_OUTPUT),
            correction_feedback=feedback,
            retry_count=1,
        )
        auditor_node(state)

        call_kwargs = mock_llm.extract_auditor_output.call_args.kwargs
        assert call_kwargs.get("correction_feedback") == feedback

    @patch("app.agents.auditor_agent.get_llm_client")
    def test_gemini_exception_captures_error(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.extract_auditor_output.side_effect = RuntimeError(
            "Gemini quota exceeded"
        )
        mock_factory.return_value = mock_llm

        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT))
        result = auditor_node(state)

        assert result["error"] is not None
        assert "auditor error" in result["error"].lower()


# ===========================================================================
# 2. Unit: contador_node
# ===========================================================================


class TestContadorNode:
    def test_skips_on_upstream_error(self):
        state = _base_state(error="upstream failure")
        result = contador_node(state)
        assert result["error"] == "upstream failure"
        assert result["contador_output"] == {}

    def test_fails_without_raw_transactions(self):
        state = _base_state(raw_transactions=[])
        result = contador_node(state)
        assert result["error"] is not None

    @patch("app.agents.contador_agent.get_llm_client")
    def test_successful_classification(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_factory.return_value = mock_llm

        state = _base_state()
        with patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("RAG down"),
        ):
            result = contador_node(state)

        assert result["error"] is None
        assert result["contador_output"]["total_debitos"] == 1_500_000
        assert result["current_agent"] == "contador"
        assert result["current_stage"] == "contador"
        assert result["interpreted_data"] == result["contador_output"]

    @patch("app.agents.contador_agent.get_llm_client")
    def test_rag_failure_is_non_fatal(self, mock_factory):
        """RAG lookup errors must not abort the node; Gemini proceeds without context."""
        mock_llm = MagicMock()
        mock_llm.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_factory.return_value = mock_llm

        state = _base_state()
        with patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("RAG down"),
        ):
            result = contador_node(state)

        assert result["error"] is None
        # Gemini must still have been called (with empty rag_context)
        call_kwargs = mock_llm.extract_contador_output.call_args.kwargs
        assert call_kwargs.get("rag_context") == []

    @patch("app.agents.contador_agent.get_llm_client")
    def test_retry_forwards_feedback_and_clears_it(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_factory.return_value = mock_llm

        feedback = "PUC 5135 no existe. Usa un código válido."
        state = _base_state(correction_feedback=feedback, retry_count=1)
        with patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("RAG down"),
        ):
            result = contador_node(state)

        call_kwargs = mock_llm.extract_contador_output.call_args.kwargs
        assert call_kwargs.get("correction_feedback") == feedback
        assert result["correction_feedback"] is None  # cleared after consume

    @patch("app.agents.contador_agent.get_llm_client")
    def test_gemini_exception_captures_error(self, mock_factory):
        mock_llm = MagicMock()
        mock_llm.extract_contador_output.side_effect = RuntimeError("timeout")
        mock_factory.return_value = mock_llm

        state = _base_state()
        with patch(
            "app.services.rag_service.get_rag_service",
            side_effect=Exception("RAG down"),
        ):
            result = contador_node(state)

        assert result["error"] is not None
        assert "contador error" in result["error"].lower()


# ===========================================================================
# 3. Unit: process_supervisor_node
# ===========================================================================


class TestProcessSupervisorNode:
    def test_sets_mode_and_routes_to_contador(self):
        state = _base_state()
        result = process_supervisor_node(state)
        assert result["mode"] == "process"
        assert result["current_agent"] == "contador"
        assert result["error"] is None

    def test_empty_transactions_produces_error(self):
        state = _base_state(raw_transactions=[])
        result = process_supervisor_node(state)
        assert result["error"] is not None
        assert (
            "staged" in result["error"].lower()
            or "transactions" in result["error"].lower()
        )

    def test_initialises_missing_validation_history(self):
        state = _base_state()
        state.pop("validation_history", None)  # type: ignore[misc]
        state["validation_history"] = []
        result = process_supervisor_node(state)
        assert isinstance(result["validation_history"], list)

    def test_initialises_retry_count_to_zero(self):
        state = _base_state(retry_count=None)  # type: ignore[arg-type]
        result = process_supervisor_node(state)
        assert result["retry_count"] == 0

    def test_tributario_partial_output_is_normalized_and_routes_to_auditor(self):
        state = _base_state(
            mode="process",
            current_agent="tributario",
            tributario_output={
                "impuestos": [],
            },
            contador_output=dict(VALID_CONTADOR_OUTPUT),
        )

        result = supervisor_node(state)

        assert result["error"] is None
        assert result["current_agent"] == "auditor"
        assert result["tributario_output"].get("fecha_analisis")
        assert result["tributario_output"].get("documento_referencia")
        assert result["tributario_output"].get("aplica_impuestos") is False
        assert str(result["tributario_output"].get("total_impuestos")) == "0"

    @patch("app.agents.routing.ingest_router.classify_document")
    @patch("app.services.excel_parser.parse_excel")
    def test_ingest_extracto_xlsx_forces_build_from_scratch_pathway(
        self,
        mock_parse_excel,
        mock_classify_document,
        tmp_path,
    ):
        xlsx_path = tmp_path / "extracto_bancario_demo.xlsx"
        xlsx_path.write_bytes(b"PK\x03\x04dummy")

        mock_parse_excel.return_value = ("extracto bancario demo", [{"dummy": True}])

        classification = MagicMock()
        classification.doc_type = DocumentType.EXTRACTO_BANCARIO
        classification.pathway = IngestPathway.WORK_WITH_EXISTING
        classification.confidence = 0.99
        classification.entity_nit = "800999888-2"
        classification.model_dump.return_value = {
            "doc_type": DocumentType.EXTRACTO_BANCARIO.value,
            "pathway": IngestPathway.WORK_WITH_EXISTING.value,
            "confidence": 0.99,
            "entity_nit": "800999888-2",
        }
        mock_classify_document.return_value = classification

        state = _base_state(
            mode="ingest",
            file_path=str(xlsx_path),
            current_agent="",
            raw_text="",
            ingest_id="",
        )

        result = supervisor_node(state)

        assert result.get("error") is None
        assert result.get("current_agent") == "ingesta"
        assert result.get("pathway") == IngestPathway.BUILD_FROM_SCRATCH.value
        assert (
            result.get("document_classification", {}).get("doc_type")
            == DocumentType.EXTRACTO_BANCARIO.value
        )


class TestPucNormalizationHelpers:
    @patch("app.agents.supervisor.SessionLocal")
    @patch("app.agents.supervisor.db_service.validate_puc_exists")
    def test_remaps_unknown_subaccount_to_active_parent(
        self,
        mock_validate,
        mock_session_local,
    ):
        db = MagicMock()
        mock_session_local.return_value = db

        row_2105 = MagicMock()
        row_2105.nombre = "Bancos Nacionales"

        def _validate_side_effect(_db, code):
            if code == "210505":
                return None
            if code == "2105":
                return row_2105
            return None

        mock_validate.side_effect = _validate_side_effect

        payload = {
            "asientos": [
                {
                    "cuenta_puc": "210505",
                    "nombre_cuenta": "",
                    "tipo_movimiento": "credito",
                    "valor": 100000,
                    "descripcion": "Obligacion financiera",
                }
            ]
        }

        normalized = _normalize_contador_puc_codes(payload)
        asiento = normalized["asientos"][0]

        assert asiento["cuenta_puc"] == "2105"
        assert asiento["nombre_cuenta"] == "Bancos Nacionales"


# ===========================================================================
# 4. Unit: validate_auditor_output_node
# ===========================================================================


class TestValidateAuditorOutputNode:
    def test_skips_on_upstream_error(self):
        state = _base_state(error="something broke")
        result = validate_auditor_output_node(state)
        assert result["error"] == "something broke"

    def test_valid_approved_output(self):
        state = _base_state(auditor_output=dict(VALID_AUDITOR_OUTPUT))
        result = validate_auditor_output_node(state)

        assert result["error"] is None
        assert result["audit_approved"] is True
        assert result["audit_rejection_reason"] is None
        assert result["correction_feedback"] is None
        assert result["current_stage"] == "audit_complete"

    def test_valid_rejected_output_sets_rejection_reason(self):
        state = _base_state(auditor_output=dict(REJECTED_AUDITOR_OUTPUT))
        result = validate_auditor_output_node(state)

        assert result["error"] is None
        assert result["audit_approved"] is False
        assert result["audit_rejection_reason"] is not None

    def test_invalid_schema_triggers_retry(self):
        """Missing required fields should trigger correction_feedback/retry."""
        state = _base_state(auditor_output=dict(INVALID_AUDITOR_OUTPUT), retry_count=0)
        result = validate_auditor_output_node(state)
        # Either a retry is scheduled or a hard error is raised — never silently passes
        assert result["correction_feedback"] is not None or result["error"] is not None

    def test_appends_to_validation_history(self):
        state = _base_state(auditor_output=dict(VALID_AUDITOR_OUTPUT))
        result = validate_auditor_output_node(state)

        history = result["validation_history"]
        assert len(history) >= 1
        assert history[-1]["agent_name"] == "auditor"
        assert history[-1]["is_valid"] is True

    def test_resets_retry_count_on_success(self):
        state = _base_state(auditor_output=dict(VALID_AUDITOR_OUTPUT), retry_count=2)
        result = validate_auditor_output_node(state)
        assert result["retry_count"] == 0


# ===========================================================================
# 5. Unit: validate_contador_output_node
# ===========================================================================


class TestValidateContadorOutputNode:
    def test_valid_output_advances_stage(self):
        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT))
        with (
            patch("app.agents.validation_rules._missing_puc_codes", return_value=[]),
            patch("app.agents.validation_rules.SessionLocal"),
        ):
            result = validate_contador_output_node(state)
        assert result["error"] is None
        assert result["correction_feedback"] is None
        assert result["current_stage"] == "validated"

    def test_unbalanced_asientos_warns_without_retry(self):
        """Unbalanced debits/credits is now a warning, not a hard error."""
        state = _base_state(
            contador_output=dict(INVALID_CONTADOR_OUTPUT), retry_count=0
        )
        with (
            patch("app.agents.validation_rules._missing_puc_codes", return_value=[]),
            patch("app.agents.validation_rules.SessionLocal") as mock_session,
        ):
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            result = validate_contador_output_node(state)
        assert result["error"] is None
        assert result["correction_feedback"] is None
        assert result["retry_count"] == 0
        history = result["validation_history"]
        assert len(history) >= 1
        assert history[-1]["is_valid"] is True

    def test_missing_puc_triggers_correction_feedback(self):
        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT), retry_count=0)
        with (
            patch(
                "app.agents.validation_rules._missing_puc_codes", return_value=["9999"]
            ),
            patch("app.agents.validation_rules.SessionLocal"),
        ):
            result = validate_contador_output_node(state)
        assert result["correction_feedback"] is not None or result["error"] is not None

    def test_missing_puc_exhausted_retries_routes_to_hitl(self):
        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT), retry_count=3)
        with (
            patch(
                "app.agents.validation_rules._missing_puc_codes", return_value=["9999"]
            ),
            patch("app.agents.validation_rules.SessionLocal"),
        ):
            result = validate_contador_output_node(state)
        assert result.get("current_agent") == "audit_review_terminal"
        assert result.get("giveup_record") is not None

    def test_appends_to_validation_history(self):
        state = _base_state(contador_output=dict(VALID_CONTADOR_OUTPUT))
        with (
            patch("app.agents.validation_rules._missing_puc_codes", return_value=[]),
            patch("app.agents.validation_rules.SessionLocal"),
        ):
            result = validate_contador_output_node(state)
        history = result["validation_history"]
        assert len(history) >= 1
        assert history[-1]["agent_name"] == "contador"

    def test_skips_on_upstream_error(self):
        state = _base_state(error="earlier failure")
        result = validate_contador_output_node(state)
        assert result["error"] == "earlier failure"


# ===========================================================================
# 6. Unit: conditional edge functions
# ===========================================================================


class TestConditionalEdges:
    def test_should_retry_contador_with_feedback(self):
        assert (
            should_retry_contador(_base_state(correction_feedback="fix this"))
            == "retry"
        )

    def test_should_not_retry_contador_without_feedback(self):
        assert should_retry_contador(_base_state(correction_feedback=None)) == "end"

    def test_should_retry_auditor_with_feedback(self):
        assert (
            should_retry_auditor(_base_state(correction_feedback="fix this")) == "retry"
        )

    def test_should_not_retry_auditor_without_feedback(self):
        assert should_retry_auditor(_base_state(correction_feedback=None)) == "end"


# ===========================================================================
# 7. Process graph structure
# ===========================================================================


class TestProcessGraphStructure:
    def test_graph_compiles(self):
        graph = create_agent_graph()
        assert graph is not None

    def test_expected_nodes_present(self):
        graph = create_agent_graph()
        nodes = {
            n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")
        }
        for expected in (
            "supervisor",
            "ingesta",
            "validate_output",
            "db_persist",
            "contador",
            "tributario",
            "auditor",
            "reportero",
            "error_terminal",
        ):
            assert expected in nodes, f"Node '{expected}' missing from process graph"

    def test_node_count(self):
        graph = create_agent_graph()
        nodes = [
            n for n in graph.get_graph().nodes if n not in ("__start__", "__end__")
        ]
        assert len(nodes) == 12


# ===========================================================================
# 8–11. E2E: process graph with mocked Gemini + mocked DB persist
# ===========================================================================


def _mock_company_settings() -> MagicMock:
    """Return a MagicMock that mimics CompanySettings with default tax rates."""
    cs = MagicMock()
    cs.tasa_retefuente_servicios = 0.04
    cs.tasa_retefuente_bienes = 0.025
    cs.tasa_retefuente_arrendamiento = 0.035
    cs.tasa_reteica = 0.00414
    cs.tasa_iva_general = 0.19
    cs.iva_responsable = True
    cs.tasa_ica = 0.00414
    cs.tasa_renta = 0.35
    return cs


def _mock_persist(state: AgentState) -> AgentState:
    """Replace db_persist_node with a lightweight stub."""
    state["db_result"] = {
        "ingest_id": state.get("ingest_id", "mock-ingest"),
        "transaction_pending_id": "mock-pending-001",
        "transaction_posted_id": "mock-posted-001",
        "journal_lines_count": 2,
        "duplicates_found": 0,
        "cuenta_puc": "5135",
        "puc_descripcion": "Servicios",
        "audit_approved": state.get("audit_approved"),
        "audit_nivel_riesgo": (state.get("auditor_output") or {}).get("nivel_riesgo"),
        "audit_puntaje_calidad": (state.get("auditor_output") or {}).get(
            "puntaje_calidad"
        ),
        "audit_hallazgos_count": len(
            (state.get("auditor_output") or {}).get("hallazgos", [])
        ),
    }
    if not state.get("result"):
        state["result"] = {}
    state["result"]["db_persisted"] = True
    state["result"]["ingest_id"] = state["db_result"]["ingest_id"]
    state["result"]["transaction_id"] = state["db_result"]["transaction_posted_id"]
    state["result"]["audit_approved"] = state.get("audit_approved")
    return state


class TestProcessGraphE2E:
    """
    Full process graph E2E: Gemini mocked, DB persist mocked.
    No PostgreSQL required.
    """

    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.tributario_agent.get_llm_client")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules.SessionLocal")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    @patch("app.agents.graph.db_persist_node", side_effect=_mock_persist)
    def test_happy_path_approved(
        self,
        _mock_db,
        _mock_puc,
        _mock_session,
        mock_cnt_factory,
        mock_aud_factory,
        mock_trib_factory,
        mock_co_settings,
    ):
        """Successful pipeline: contador → validate → auditor → validate → persist."""
        mock_co_settings.return_value = _mock_company_settings()

        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_cnt_factory.return_value = mock_cnt

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_aud_factory.return_value = mock_aud

        mock_trib = MagicMock()
        mock_trib.justify_tax_analysis.return_value = MagicMock(
            referencias=["Art. 383 ET"], justificacion="ok", confirma_tasas=True
        )
        mock_trib_factory.return_value = mock_trib

        # RAG service not needed in this test
        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            graph = create_agent_graph()
            state = _base_state()
            final = graph.invoke(state)

        assert final.get("error") is None
        assert final["audit_approved"] is True
        assert final["db_result"]["audit_approved"] is True
        assert final["db_result"]["journal_lines_count"] == 2
        assert len(final["validation_history"]) >= 2  # contador + auditor validated

    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.tributario_agent.get_llm_client")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules.SessionLocal")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    @patch("app.agents.graph.db_persist_node", side_effect=_mock_persist)
    def test_rejected_audit_triggers_self_correction_then_approves(
        self,
        _mock_db,
        _mock_puc,
        _mock_session,
        mock_cnt_factory,
        mock_aud_factory,
        mock_trib_factory,
        mock_co_settings,
    ):
        """
        Self-correction cycle: auditor rejects once → supervisor routes back to
        contador with feedback → contador corrects → auditor approves → db_persist.
        Pipeline completes without error; audit_approved=True.
        """
        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_cnt_factory.return_value = mock_cnt

        # Reject on first call, approve on second (after self-correction)
        audit_calls = {"n": 0}

        def auditor_side_effect(*args, **kwargs):
            audit_calls["n"] += 1
            if audit_calls["n"] == 1:
                return dict(REJECTED_AUDITOR_OUTPUT)
            return dict(VALID_AUDITOR_OUTPUT)

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.side_effect = auditor_side_effect
        mock_aud_factory.return_value = mock_aud

        mock_trib = MagicMock()
        mock_trib.justify_tax_analysis.return_value = MagicMock(
            referencias=["Art. 383 ET"], justificacion="ok", confirma_tasas=True
        )
        mock_trib_factory.return_value = mock_trib
        mock_co_settings.return_value = _mock_company_settings()

        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            graph = create_agent_graph()
            final = graph.invoke(_base_state())

        assert final.get("error") is None, final.get("error")
        assert final["audit_approved"] is True
        assert final["db_result"] is not None
        assert (
            audit_calls["n"] == 2
        ), "Auditor should have been called twice (reject + approve)"

    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.tributario_agent.get_llm_client")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules.SessionLocal")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    @patch("app.agents.graph.db_persist_node", side_effect=_mock_persist)
    def test_contador_retry_then_success(
        self,
        _mock_db,
        _mock_puc,
        _mock_session,
        mock_cnt_factory,
        mock_aud_factory,
        mock_trib_factory,
        mock_co_settings,
    ):
        """
        First contador call returns an unbalanced output → validate triggers retry.
        Second call returns valid output → pipeline continues to auditor.
        """
        call_count = {"n": 0}

        def side_effect_contador(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return dict(INVALID_CONTADOR_OUTPUT_HARD)
            return dict(VALID_CONTADOR_OUTPUT)

        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.side_effect = side_effect_contador
        mock_cnt_factory.return_value = mock_cnt

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_aud_factory.return_value = mock_aud

        mock_trib = MagicMock()
        mock_trib.justify_tax_analysis.return_value = MagicMock(
            referencias=["Art. 383 ET"], justificacion="ok", confirma_tasas=True
        )
        mock_trib_factory.return_value = mock_trib
        mock_co_settings.return_value = _mock_company_settings()

        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            graph = create_agent_graph()
            final = graph.invoke(_base_state())

        assert final.get("error") is None
        assert call_count["n"] >= 2, "Expected at least one retry of contador"
        # Validation history has at least one failed + one passed for contador
        contador_history = [
            h for h in final["validation_history"] if h["agent_name"] == "contador"
        ]
        assert any(not h["is_valid"] for h in contador_history)
        assert any(h["is_valid"] for h in contador_history)

    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.tributario_agent.get_llm_client")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules.SessionLocal")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    @patch("app.agents.graph.db_persist_node", side_effect=_mock_persist)
    def test_auditor_retry_then_success(
        self,
        _mock_db,
        _mock_puc,
        _mock_session,
        mock_cnt_factory,
        mock_aud_factory,
        mock_trib_factory,
        mock_co_settings,
    ):
        """
        First auditor call returns invalid schema → validate triggers retry.
        Second call returns valid output → pipeline persists.
        """
        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_cnt_factory.return_value = mock_cnt

        aud_call_count = {"n": 0}

        def side_effect_auditor(*args, **kwargs):
            aud_call_count["n"] += 1
            if aud_call_count["n"] == 1:
                return dict(INVALID_AUDITOR_OUTPUT)
            return dict(VALID_AUDITOR_OUTPUT)

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.side_effect = side_effect_auditor
        mock_aud_factory.return_value = mock_aud

        mock_trib = MagicMock()
        mock_trib.justify_tax_analysis.return_value = MagicMock(
            referencias=["Art. 383 ET"], justificacion="ok", confirma_tasas=True
        )
        mock_trib_factory.return_value = mock_trib
        mock_co_settings.return_value = _mock_company_settings()

        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            graph = create_agent_graph()
            final = graph.invoke(_base_state())

        assert final.get("error") is None
        assert aud_call_count["n"] >= 2, "Expected at least one retry of auditor"

    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    def test_contador_exhausted_retries_routes_to_hitl(
        self, _mock_puc, mock_cnt_factory
    ):
        """When all retries are exhausted, graph routes to HITL audit_review_terminal."""
        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(
            INVALID_CONTADOR_OUTPUT_HARD
        )
        mock_cnt_factory.return_value = mock_cnt

        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            graph = create_agent_graph()
            final = graph.invoke(_base_state())

        assert (
            final.get("giveup_record") is not None
            or final.get("current_agent") == "audit_review_terminal"
        )

    @patch("app.services.db_service.get_company_settings")
    @patch("app.agents.tributario_agent.get_llm_client")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules.SessionLocal")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    @patch("app.agents.graph.db_persist_node", side_effect=_mock_persist)
    def test_audit_result_propagated_to_result_dict(
        self,
        _mock_db,
        _mock_puc,
        _mock_session,
        mock_cnt_factory,
        mock_aud_factory,
        mock_trib_factory,
        mock_co_settings,
    ):
        """audit_approved and audit_nivel_riesgo must surface in final result."""
        mock_co_settings.return_value = _mock_company_settings()

        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_cnt_factory.return_value = mock_cnt

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_aud_factory.return_value = mock_aud

        mock_trib = MagicMock()
        mock_trib.justify_tax_analysis.return_value = MagicMock(
            referencias=["Art. 383 ET"], justificacion="ok", confirma_tasas=True
        )
        mock_trib_factory.return_value = mock_trib

        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            graph = create_agent_graph()
            final = graph.invoke(_base_state())

        db_result = final.get("db_result", {})
        assert db_result.get("audit_approved") is True
        assert db_result.get("audit_nivel_riesgo") == "bajo"
        assert Decimal(str(db_result.get("audit_puntaje_calidad"))) == Decimal("95.0")
        assert db_result.get("audit_hallazgos_count") == 0


# ===========================================================================
# 12. DB integration: full pipeline with real PostgreSQL
#     (skipped automatically when PostgreSQL is unavailable)
# ===========================================================================


def _check_postgres_available() -> bool:
    """Return True when PostgreSQL is reachable (2-second connect timeout)."""
    from sqlalchemy import create_engine

    eng = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"connect_timeout": 2},
    )
    try:
        conn = eng.connect()
        conn.close()
        eng.dispose()
        return True
    except Exception:
        return False


class TestProcessGraphDBIntegration:
    """
    Runs the full process graph against the real database.
    Gemini is mocked; all DB calls are real.

    Requires:
      - DATABASE_URL pointing to a running PostgreSQL instance
      - PUC codes 5135 and 2205 seeded in cuenta_puc table
      - A TransactionPending row available (created inline)
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_postgres(self):
        if not _check_postgres_available():
            pytest.skip(f"PostgreSQL not available at {DATABASE_URL!r}")

    @pytest.fixture(autouse=True)
    def _seed_puc_and_pending(self):
        """
        Ensure PUC codes 5135 and 2205 exist and create a throwaway
        TransactionPending row for the test.  Rolls back on teardown.
        """
        from datetime import datetime, timezone

        from app.core.database import SessionLocal
        from app.models.database import CuentaPUC, NaturalezaCuenta
        from app.services import db_service

        db = SessionLocal()
        try:
            # Ensure required PUC codes exist
            for code, nombre, clase, naturaleza in [
                ("5135", "Servicios", 5, NaturalezaCuenta.DEBITO),
                ("2205", "Proveedores nacionales", 2, NaturalezaCuenta.CREDITO),
            ]:
                existing = db.query(CuentaPUC).filter(CuentaPUC.codigo == code).first()
                if not existing:
                    db.add(
                        CuentaPUC(
                            codigo=code,
                            nombre=nombre,
                            clase=clase,
                            naturaleza=naturaleza,
                            activa=True,
                        )
                    )

            # Create an IngestJob + TransactionPending to link the process run to
            ingest_job = db_service.create_ingest_job(
                db, "test_e2e_auditor.pdf", "/tmp/test.pdf"
            )
            pending = db_service.create_transaction_pending(
                db,
                ingest_id=str(ingest_job.id),
                fecha=datetime(2026, 1, 15, tzinfo=timezone.utc),
                nit_emisor="900123456",
                nit_receptor="800999888",
                total=Decimal("1500000"),
                descripcion="Servicio de consultoría contable",
                items=[],
                raw_data={},
            )
            db.flush()
            self._ingest_id = str(ingest_job.id)
            self._pending_id = str(pending.id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    def test_full_pipeline_persists_records(
        self, _mock_puc, mock_cnt_factory, mock_aud_factory
    ):
        """End-to-end pipeline writes TransactionPosted + JournalEntryLines to DB."""
        from app.core.database import SessionLocal
        from app.models.database import JournalEntryLine, TransactionPosted

        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_cnt_factory.return_value = mock_cnt

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.return_value = dict(VALID_AUDITOR_OUTPUT)
        mock_aud_factory.return_value = mock_aud

        with patch(
            "app.services.rag_service.get_rag_service", side_effect=Exception("no RAG")
        ):
            result = invoke_accounting_pipeline(
                ingest_id=self._ingest_id,
                raw_transactions=list(VALID_RAW_TRANSACTIONS),
                pending_transaction_id=self._pending_id,
            )

        assert result.get("error") is None, f"Pipeline error: {result.get('error')}"
        assert result.get("db_result") is not None

        db_res = result["db_result"]
        assert db_res["transaction_posted_id"] is not None
        assert db_res["audit_approved"] is True
        assert db_res["audit_nivel_riesgo"] == "bajo"
        assert db_res["journal_lines_count"] >= 2

        # Verify records exist in DB
        db = SessionLocal()
        try:
            posted = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.id == db_res["transaction_posted_id"])
                .first()
            )
            assert posted is not None
            assert posted.cuenta_puc == "5135"

            lines = (
                db.query(JournalEntryLine)
                .filter(
                    JournalEntryLine.transaction_posted_id
                    == db_res["transaction_posted_id"]
                )
                .all()
            )
            assert len(lines) >= 2

            total_debito = sum(ln.debito for ln in lines)
            total_credito = sum(ln.credito for ln in lines)
            assert (
                total_debito == total_credito
            ), f"Partida doble violated: debitos={total_debito} creditos={total_credito}"

            # Auditor output stored in agent_reasoning
            assert posted.agent_reasoning is not None
            assert posted.agent_reasoning.get("auditor", {}).get("aprobado") is True
        finally:
            db.close()

    @patch("app.agents.tributario_agent.get_llm_client")
    @patch("app.agents.auditor_agent.get_llm_client")
    @patch("app.agents.contador_agent.get_llm_client")
    @patch("app.agents.validation_rules._missing_puc_codes", return_value=[])
    def test_rejected_audit_stored_with_findings(
        self, _mock_puc, mock_cnt_factory, mock_aud_factory, mock_trib_factory
    ):
        """Phase 4: repeated audit rejection exhausts budget → error_terminal, no DB persist.

        The old behavior (persist with aprobado=False) was replaced by the pinpointed
        self-improvement loop: budget exhaustion routes to error_terminal and records
        a giveup_record instead of blindly persisting a bad result.
        """
        mock_cnt = MagicMock()
        mock_cnt.extract_contador_output.return_value = dict(VALID_CONTADOR_OUTPUT)
        mock_cnt_factory.return_value = mock_cnt

        mock_aud = MagicMock()
        mock_aud.extract_auditor_output.return_value = dict(REJECTED_AUDITOR_OUTPUT)
        mock_aud_factory.return_value = mock_aud

        mock_trib = MagicMock()
        mock_trib.justify_tax_analysis.return_value = MagicMock(
            referencias=["Art. 383 ET"], justificacion="ok", confirma_tasas=True
        )
        mock_trib_factory.return_value = mock_trib

        mock_company_row = MagicMock()
        mock_company_row.tasa_retefuente_servicios = 0.11
        mock_company_row.tasa_retefuente_bienes = 0.025
        mock_company_row.tasa_retefuente_arrendamiento = 0.035
        mock_company_row.tasa_reteica = 0.00414
        mock_company_row.tasa_iva_general = 0.19
        mock_company_row.iva_responsable = True

        with (
            patch(
                "app.agents.tributario_agent.get_rag_service",
                side_effect=Exception("no RAG"),
            ),
            patch(
                "app.services.rag_service.get_rag_service",
                side_effect=Exception("no RAG"),
            ),
            patch(
                "app.services.db_service.get_company_settings",
                return_value=mock_company_row,
            ),
        ):
            result = invoke_accounting_pipeline(
                ingest_id=self._ingest_id,
                raw_transactions=list(VALID_RAW_TRANSACTIONS),
                pending_transaction_id=self._pending_id,
            )

        # Phase 4: budget exhaustion routes to error_terminal — no DB persist.
        assert result.get("audit_approved") is False
        assert (
            result.get("giveup_record") is not None
        ), "giveup_record must be set after retry budget exhaustion"
        # persist was refused — db_result should not contain a posted transaction
        db_res = result.get("db_result") or {}
        assert db_res.get("transaction_posted_id") is None


# ===========================================================================
# 13. Auditor prompt — whitelist for legitimate duplicate cuenta_puc
# ===========================================================================


class TestAuditorPromptWhitelist:
    """The auditor prompt must explicitly whitelist patterns that are legitimate
    in Colombian accounting (e.g. 2368 used twice in the same asiento for
    Reteica + ICA por pagar) so the LLM does not reject them as duplicates.
    """

    def _build_prompt(self) -> str:
        from app.core.llm_client import LLMClient

        # Capture the prompt string passed to _invoke without contacting any
        # provider. We bypass __init__ so we don't require API keys in tests.
        client = LLMClient.__new__(LLMClient)
        captured: dict[str, str] = {}

        def _fake_invoke(_schema, prompt):  # type: ignore[no-untyped-def]
            captured["prompt"] = prompt
            return MagicMock(
                fecha_auditoria="2026-01-16",
                documento_referencia="X",
                aprobado=True,
                nivel_riesgo="bajo",
                hallazgos=[],
                puntaje_calidad=100.0,
                resumen="ok",
            )

        client._invoke = _fake_invoke  # type: ignore[assignment]
        client._as_dict = lambda x: {  # type: ignore[assignment]
            "fecha_auditoria": x.fecha_auditoria,
            "documento_referencia": x.documento_referencia,
            "aprobado": x.aprobado,
            "nivel_riesgo": x.nivel_riesgo,
            "hallazgos": x.hallazgos,
            "puntaje_calidad": x.puntaje_calidad,
            "resumen": x.resumen,
        }
        client.extract_auditor_output(
            contador_output=dict(VALID_CONTADOR_OUTPUT),
            raw_transactions=list(VALID_RAW_TRANSACTIONS),
        )
        return captured["prompt"]

    def test_prompt_includes_valid_patterns_section(self):
        prompt = self._build_prompt()
        assert "PATRONES VÁLIDOS" in prompt

    def test_prompt_explicitly_allows_repeated_2368(self):
        prompt = self._build_prompt()
        assert "2368" in prompt
        assert "Reteica" in prompt or "ICA" in prompt

    def test_prompt_lists_explicit_rejection_criteria(self):
        prompt = self._build_prompt()
        assert "RECHAZO" in prompt
        assert "desbalanceado" in prompt
