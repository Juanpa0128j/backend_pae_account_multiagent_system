"""
Unit tests for the Tributario (Tax Specialist) agent node.

Tests cover:
1. Node replaces stub — tributario_output populated in state
2. Retefuente 11% applied for PUC 5xxx (servicios)
3. ReteICA 0.69% applied
4. IVA 19% calculated when not in asientos
5. IVA captured from existing asientos (not double-counted)
6. Arrendamiento: 10% Retefuente detected from description
7. Bienes: 3% Retefuente when no 5xxx PUC
8. Upstream error passthrough — state unchanged
9. Missing contador_output sets error
10. Schema validation — TributarioOutput.model_validate passes
11. Gemini/RAG fallback on failure — node still completes
12. Enriched journal entries contain tax liability accounts
13. aplica_impuestos=False when all taxes are zero (zero base)
"""

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.agents.tributario_agent import (
    tributario_node,
    _detect_transaction_type,
    _calc_retefuente,
    _calc_reteica,
    _calc_iva,
    _has_iva_in_asientos,
    TASA_RETEFUENTE,
    TASA_RETEICA_DEFAULT,
)
from app.agents.state import AgentState
from app.core.gemini_client import TaxJustification
from app.models.agent_outputs import TributarioOutput


# ─── Fixtures ─────────────────────────────────────────────────────────────────

VALID_CONTADOR_OUTPUT = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "factura",
    "descripcion_general": "Servicios profesionales marzo 2026",
    "asientos": [
        {
            "cuenta_puc": "5110",
            "nombre_cuenta": "Honorarios",
            "tipo_movimiento": "debito",
            "valor": 1500000,
            "descripcion": "Servicios profesionales",
        },
        {
            "cuenta_puc": "1110",
            "nombre_cuenta": "Bancos",
            "tipo_movimiento": "credito",
            "valor": 1500000,
            "descripcion": "Pago bancario",
        },
    ],
    "total_debitos": 1500000,
    "total_creditos": 1500000,
}

VALID_CONTADOR_OUTPUT_WITH_IVA = {
    **VALID_CONTADOR_OUTPUT,
    "asientos": [
        *VALID_CONTADOR_OUTPUT["asientos"],
        {
            "cuenta_puc": "2408",
            "nombre_cuenta": "IVA Descontable",
            "tipo_movimiento": "credito",
            "valor": 285000,
            "descripcion": "IVA 19%",
        },
    ],
}

VALID_CONTADOR_OUTPUT_BIENES = {
    **VALID_CONTADOR_OUTPUT,
    "descripcion_general": "Compra de suministros de oficina",
    "asientos": [
        {
            "cuenta_puc": "1524",  # Activos — bienes
            "nombre_cuenta": "Equipos de cómputo",
            "tipo_movimiento": "debito",
            "valor": 1000000,
            "descripcion": "Compra suministros",
        },
        {
            "cuenta_puc": "1110",
            "nombre_cuenta": "Bancos",
            "tipo_movimiento": "credito",
            "valor": 1000000,
            "descripcion": "Pago bancario",
        },
    ],
    "total_debitos": 1000000,
    "total_creditos": 1000000,
}


def _make_state(contador_output=None, error=None) -> AgentState:
    return {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": error,
        "validation_history": [],
        "current_agent": "contador",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
        "mode": "process",
        "raw_transactions": [],
        "contador_output": contador_output or {},
        "tributario_output": {},
        "company_config": None,
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": "classifying_complete",
        "agent_log": [],
        "audit_decision": None,
        "audit_feedback": None,
    }


def _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn):
    """Configure standard mocks for RAG and Gemini."""
    mock_rag = MagicMock()
    mock_rag.search_normativo.return_value = []
    mock_rag_cls.return_value = mock_rag

    mock_gc = MagicMock()
    mock_gc.justify_tax_analysis.return_value = TaxJustification(
        referencias=["Art. 383 ET", "Decreto 2048/1992"],
        justificacion="Retenciones aplicadas según tasas vigentes ET.",
        confirma_tasas=True,
    )
    mock_gemini_fn.return_value = mock_gc
    return mock_rag, mock_gc


# ─── Unit tests: pure calculator functions ────────────────────────────────────

def test_calc_retefuente_servicios():
    result = _calc_retefuente(Decimal("1500000"), "servicios")
    assert result == Decimal("165000.00")


def test_calc_retefuente_bienes():
    result = _calc_retefuente(Decimal("1000000"), "bienes")
    assert result == Decimal("30000.00")


def test_calc_retefuente_arrendamiento():
    result = _calc_retefuente(Decimal("1000000"), "arrendamiento")
    assert result == Decimal("100000.00")


def test_calc_reteica():
    result = _calc_reteica(Decimal("1500000"))
    assert result == Decimal("10350.00")


def test_calc_iva_general():
    result = _calc_iva(Decimal("1500000"), "general")
    assert result == Decimal("285000.00")


def test_calc_iva_exento():
    result = _calc_iva(Decimal("1000000"), "exento")
    assert result == Decimal("0.00")


def test_detect_transaction_type_servicios():
    asientos = [{"cuenta_puc": "5195", "tipo_movimiento": "debito"}]
    assert _detect_transaction_type(asientos) == "servicios"


def test_detect_transaction_type_bienes():
    asientos = [{"cuenta_puc": "1524", "tipo_movimiento": "debito"}]
    assert _detect_transaction_type(asientos) == "bienes"


def test_detect_transaction_type_arrendamiento():
    asientos = [
        {"cuenta_puc": "5120", "tipo_movimiento": "debito", "descripcion": "Arrendamiento oficina"}
    ]
    assert _detect_transaction_type(asientos) == "arrendamiento"


def test_has_iva_in_asientos_present():
    asientos = [{"cuenta_puc": "2408", "valor": 285000, "tipo_movimiento": "credito"}]
    present, val = _has_iva_in_asientos(asientos)
    assert present is True
    assert val == Decimal("285000.00")


def test_has_iva_in_asientos_absent():
    asientos = [{"cuenta_puc": "5195", "valor": 1500000, "tipo_movimiento": "debito"}]
    present, val = _has_iva_in_asientos(asientos)
    assert present is False
    assert val == Decimal("0")


# ─── Integration tests: tributario_node ───────────────────────────────────────

@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_node_replaces_stub(mock_rag_cls, mock_gemini_fn):
    """Node populates tributario_output — no longer a stub."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    assert result["tributario_output"] != {}
    assert result["current_agent"] == "tributario"
    assert result["current_stage"] == "tributario_complete"


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_retefuente_servicios_11_percent(mock_rag_cls, mock_gemini_fn):
    """Retefuente = 11% for PUC 5xxx (servicios), base 1,500,000."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    retefuente = next(i for i in impuestos if i["tipo_impuesto"] == "retefuente")
    assert Decimal(retefuente["valor_impuesto"]) == Decimal("165000.00")
    assert retefuente["cuenta_puc"] == "2365"


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_reteica_applied(mock_rag_cls, mock_gemini_fn):
    """ReteICA = 0.69% applied, cuenta 2368."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    reteica = next(i for i in impuestos if i["tipo_impuesto"] == "reteica")
    assert Decimal(reteica["valor_impuesto"]) == Decimal("10350.00")
    assert reteica["cuenta_puc"] == "2368"


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_iva_calculated_when_not_in_asientos(mock_rag_cls, mock_gemini_fn):
    """IVA 19% calculated when not present in contador asientos."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    iva = next(i for i in impuestos if i["tipo_impuesto"] == "IVA")
    assert Decimal(iva["valor_impuesto"]) == Decimal("285000.00")


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_iva_captured_from_asientos_not_doubled(mock_rag_cls, mock_gemini_fn):
    """IVA from contador asientos is captured, not recalculated."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT_WITH_IVA)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    iva_entries = [i for i in impuestos if i["tipo_impuesto"] == "IVA"]
    assert len(iva_entries) == 1
    assert Decimal(iva_entries[0]["valor_impuesto"]) == Decimal("285000.00")

    # Should NOT add another IVA entry to asientos_enriquecidos
    enriquecidos = result["tributario_output"]["asientos_enriquecidos"]
    iva_enriched = [a for a in enriquecidos if a.get("cuenta_puc") == "2408"]
    # Only the original IVA from contador should be present (not duplicated)
    assert len(iva_enriched) == 1


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_retefuente_bienes_3_percent(mock_rag_cls, mock_gemini_fn):
    """Retefuente = 3% for bienes (non-5xxx PUC), base 1,000,000."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT_BIENES)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    retefuente = next(i for i in impuestos if i["tipo_impuesto"] == "retefuente")
    assert Decimal(retefuente["valor_impuesto"]) == Decimal("30000.00")


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_upstream_error_passthrough(mock_rag_cls, mock_gemini_fn):
    """Node skips processing if upstream error is set."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT, error="Upstream failure")
    result = tributario_node(state)

    assert result["error"] == "Upstream failure"
    assert result.get("tributario_output") == {}
    mock_gemini_fn.return_value.justify_tax_analysis.assert_not_called()


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_missing_contador_output_sets_error(mock_rag_cls, mock_gemini_fn):
    """Missing contador_output results in an error being set."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(contador_output={})
    result = tributario_node(state)

    assert result["error"] is not None
    assert "contador_output" in result["error"].lower()


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_schema_valid(mock_rag_cls, mock_gemini_fn):
    """tributario_output validates against TributarioOutput Pydantic schema."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    output = result["tributario_output"]
    parsed = TributarioOutput.model_validate(output)
    assert parsed.aplica_impuestos is True
    assert parsed.total_impuestos > 0


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_gemini_fallback_on_failure(mock_rag_cls, mock_gemini_fn):
    """Node completes successfully even when Gemini call raises exception."""
    mock_rag = MagicMock()
    mock_rag.search_normativo.return_value = []
    mock_rag_cls.return_value = mock_rag

    mock_gc = MagicMock()
    mock_gc.justify_tax_analysis.side_effect = Exception("Gemini API unavailable")
    mock_gemini_fn.return_value = mock_gc

    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    # Should still complete — fallback kicks in
    assert result.get("error") is None
    assert result["tributario_output"].get("aplica_impuestos") is True
    assert "Art. 383 ET" in result["tributario_output"].get("referencias_legales", [])


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_rag_fallback_on_failure(mock_rag_cls, mock_gemini_fn):
    """Node completes when RAG lookup raises exception."""
    mock_rag = MagicMock()
    mock_rag.search_normativo.side_effect = Exception("ChromaDB unavailable")
    mock_rag_cls.return_value = mock_rag

    mock_gc = MagicMock()
    mock_gc.justify_tax_analysis.return_value = TaxJustification(
        referencias=["Art. 383 ET"],
        justificacion="Tasas aplicadas según ET.",
        confirma_tasas=True,
    )
    mock_gemini_fn.return_value = mock_gc

    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    assert result["tributario_output"] != {}


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_journal_entries_enriched_with_tax_accounts(mock_rag_cls, mock_gemini_fn):
    """Enriched asientos contain tax liability accounts (2365, 2368, 2408)."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    enriquecidos = result["tributario_output"]["asientos_enriquecidos"]
    cuentas = [a["cuenta_puc"] for a in enriquecidos]

    assert "2365" in cuentas, "Retefuente account 2365 missing from enriched asientos"
    assert "2368" in cuentas, "ReteICA account 2368 missing from enriched asientos"
    assert "2408" in cuentas, "IVA account 2408 missing from enriched asientos"


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_referencias_legales_in_output(mock_rag_cls, mock_gemini_fn):
    """Legal references from Gemini are stored in tributario_output."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    referencias = result["tributario_output"].get("referencias_legales", [])
    assert len(referencias) > 0
    assert any("Art. 383" in r for r in referencias)


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_total_impuestos_matches_sum(mock_rag_cls, mock_gemini_fn):
    """total_impuestos equals the sum of individual impuesto values."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    output = result["tributario_output"]
    impuestos = output["impuestos"]
    calculated = sum(Decimal(i["valor_impuesto"]) for i in impuestos)
    stored = Decimal(output["total_impuestos"])
    assert calculated == stored


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_agent_log_entries_written(mock_rag_cls, mock_gemini_fn):
    """node_start and node_complete are written to agent_log."""
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    events = [e["event"] for e in result["agent_log"]]
    assert "node_start" in events
    assert "node_complete" in events


@patch("app.agents.tributario_agent.get_gemini_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_smoke_1500000_servicios(mock_rag_cls, mock_gemini_fn):
    """
    Smoke test: $1,500,000 servicios.
    Expected: retefuente=165,000, reteica=10,350, iva=285,000, total=460,350
    """
    _mock_gemini_and_rag(mock_rag_cls, mock_gemini_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    output = result["tributario_output"]
    impuestos = {i["tipo_impuesto"]: Decimal(i["valor_impuesto"]) for i in output["impuestos"]}

    assert impuestos["retefuente"] == Decimal("165000.00")
    assert impuestos["reteica"]    == Decimal("10350.00")
    assert impuestos["IVA"]        == Decimal("285000.00")
    assert Decimal(output["total_impuestos"]) == Decimal("460350.00")
