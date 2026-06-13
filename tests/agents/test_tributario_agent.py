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

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agents.tributario_agent import (
    tributario_node,
    _detect_transaction_type,
    _calc_retefuente,
    _calc_reteica,
    _calc_iva,
    _has_iva_in_asientos,
    _nota_is_venta,
)
from app.agents.state import AgentState
from app.models.llm_schemas import TaxJustification
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
        {
            "cuenta_puc": "5110",
            "nombre_cuenta": "Honorarios",
            "tipo_movimiento": "debito",
            "valor": 1500000,
            "descripcion": "Servicios profesionales",
        },
        {
            "cuenta_puc": "2408",
            "nombre_cuenta": "IVA Descontable",
            "tipo_movimiento": "debito",  # IVA descontable is an asset (debit) for the buyer
            "valor": 285000,
            "descripcion": "IVA 19%",
        },
        {
            "cuenta_puc": "1110",
            "nombre_cuenta": "Bancos",
            "tipo_movimiento": "credito",
            "valor": 1785000,  # base (1,500,000) + IVA (285,000) = total paid to vendor
            "descripcion": "Pago bancario",
        },
    ],
    "total_debitos": 1785000,
    "total_creditos": 1785000,
}

VALID_CONTADOR_OUTPUT_BIENES = {
    **VALID_CONTADOR_OUTPUT,
    "descripcion_general": "Compra de suministros de oficina",
    "asientos": [
        {
            "cuenta_puc": "1524",  # Activos — bienes
            "nombre_cuenta": "Equipos de cómputo",
            "tipo_movimiento": "debito",
            # Base $2,000,000 sits above the 27-UVT minimum for bienes ($1,414,098
            # at UVT 2026 = $52,374). Below it the agent zeroes retefuente.
            "valor": 2000000,
            "descripcion": "Compra suministros",
        },
        {
            "cuenta_puc": "1110",
            "nombre_cuenta": "Bancos",
            "tipo_movimiento": "credito",
            "valor": 2000000,
            "descripcion": "Pago bancario",
        },
    ],
    "total_debitos": 2000000,
    "total_creditos": 2000000,
}


def _make_state(contador_output=None, error=None, company_config=None) -> AgentState:
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
        "company_config": company_config,
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": "classifying_complete",
        "agent_log": [],
        "audit_decision": None,
        "audit_feedback": None,
    }


def _mock_llm_and_rag(mock_rag_cls, mock_llm_fn):
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
    mock_llm_fn.return_value = mock_gc
    return mock_rag, mock_gc


# ─── Unit tests: pure calculator functions ────────────────────────────────────


def test_calc_retefuente_servicios():
    # 4% servicios declarantes — Art. 401 ET, tabla 2026
    result = _calc_retefuente(Decimal("1500000"), "servicios")
    assert result == Decimal("60000.00")


def test_calc_retefuente_bienes():
    # 2.5% compras declarantes — Art. 401 ET, tabla 2026
    result = _calc_retefuente(Decimal("1000000"), "bienes")
    assert result == Decimal("25000.00")


def test_calc_retefuente_arrendamiento():
    # 3.5% arrendamiento inmuebles — Art. 401 ET, tabla 2026
    result = _calc_retefuente(Decimal("1000000"), "arrendamiento")
    assert result == Decimal("35000.00")


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


def test_detect_transaction_type_honorarios_by_puc():
    # PUC 5110/511505/511510 → honorarios (11%), not servicios (4%).
    for puc in ("5110", "511505", "511510"):
        asientos = [{"cuenta_puc": puc, "tipo_movimiento": "debito"}]
        assert _detect_transaction_type(asientos) == "honorarios", puc


def test_detect_transaction_type_honorarios_by_keyword():
    # factura_compra with an asesoría concept and no expense line yet → honorarios.
    assert (
        _detect_transaction_type(
            [],
            doc_type="factura_compra",
            descripcion_general="Asesoría jurídica externa",
        )
        == "honorarios"
    )


# ─── #6 Notas crédito/débito: dirección venta vs compra ───────────────────────


def _nota_state(doc_type, *, emisor_nit=None, receptor_nit=None, company_nit=None):
    st = _make_state(VALID_CONTADOR_OUTPUT)
    st["document_classification"] = {"doc_type": doc_type}
    st["source_document"] = {"nit_emisor": emisor_nit, "nit_receptor": receptor_nit}
    st["company_nit"] = company_nit
    return st


def test_nota_credito_emisor_is_tenant_is_venta():
    # The company issued the credit note (emisor == tenant) → adjusts a SALE.
    st = _nota_state("nota_credito", emisor_nit="900123456-7", company_nit="900123456")
    assert _nota_is_venta(st) is True


def test_nota_debito_receptor_is_tenant_is_compra():
    # The company received the debit note from a supplier → adjusts a PURCHASE.
    st = _nota_state(
        "nota_debito",
        emisor_nit="800111222",
        receptor_nit="900123456",
        company_nit="900123456-7",
    )
    assert _nota_is_venta(st) is False


def test_nota_direction_unknown_returns_none():
    # Neither emisor nor receptor matches the tenant → undeterminable.
    st = _nota_state(
        "nota_credito", emisor_nit="111", receptor_nit="222", company_nit="900123456"
    )
    assert _nota_is_venta(st) is None


def test_non_nota_returns_none():
    st = _nota_state("factura_venta", emisor_nit="900123456", company_nit="900123456")
    assert _nota_is_venta(st) is None


def test_nota_is_venta_does_not_use_nit_receptor_as_tenant():
    # state["company_nit"] is None; nit_receptor="900111222" is the counterpart NIT,
    # nit_emisor="800999888" is the supplier. Without a real company_nit fallback,
    # direction must be None (indeterminate) — the bug would wrongly treat
    # nit_receptor as the tenant and return False (compra).
    st = _make_state(VALID_CONTADOR_OUTPUT)
    st["document_classification"] = {"doc_type": "nota_credito"}
    st["source_document"] = {
        "nit_emisor": "800999888",
        "nit_receptor": "900111222",
    }
    st["company_nit"] = None
    # No raw_transactions with company_nit either — truly unknown tenant.
    st["raw_transactions"] = [{"nit_receptor": "900111222", "monto": 100}]
    assert _nota_is_venta(st) is None


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_nota_credito_venta_routes_iva_generado(mock_rag_cls, mock_llm_fn):
    """A sale-side credit note routes IVA to 240805 (generado), not 240802."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    state["document_classification"] = {"doc_type": "nota_credito"}
    state["source_document"] = {"nit_emisor": "900123456", "nit_receptor": "800111222"}
    state["company_nit"] = "900123456"
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    iva = next((i for i in impuestos if i["tipo_impuesto"] == "IVA"), None)
    assert iva is not None
    assert iva["cuenta_puc"] == "240805"


def test_detect_transaction_type_arrendamiento():
    asientos = [
        {
            "cuenta_puc": "5120",
            "tipo_movimiento": "debito",
            "descripcion": "Arrendamiento oficina",
        }
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


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_node_replaces_stub(mock_rag_cls, mock_llm_fn):
    """Node populates tributario_output — no longer a stub."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    assert result["tributario_output"] != {}
    assert result["current_agent"] == "tributario"
    assert result["current_stage"] == "tributario_complete"


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_retefuente_honorarios_11_percent(mock_rag_cls, mock_llm_fn):
    """Retefuente = 11% for PUC 5110 (honorarios), base 1,500,000 → 165,000.

    Honorarios (PUC 5110/511505/511510) are withheld at 11% (Art. 392 ET), NOT
    the 4% servicios rate. No base mínima applies — retención desde el 1.er peso.
    """
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    retefuente = next(i for i in impuestos if i["tipo_impuesto"] == "retefuente")
    assert Decimal(retefuente["valor_impuesto"]) == Decimal("165000.00")
    assert retefuente["cuenta_puc"] == "2365"


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_retefuente_servicios_4_percent(mock_rag_cls, mock_llm_fn):
    """Retefuente = 4% for a genuine servicios PUC (511525 servicios técnicos)."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    contador = {
        **VALID_CONTADOR_OUTPUT,
        "asientos": [
            {
                "cuenta_puc": "511525",
                "nombre_cuenta": "Servicios técnicos",
                "tipo_movimiento": "debito",
                "valor": 1500000,
                "descripcion": "Servicio técnico",
            },
            {
                "cuenta_puc": "1110",
                "nombre_cuenta": "Bancos",
                "tipo_movimiento": "credito",
                "valor": 1500000,
                "descripcion": "Pago",
            },
        ],
    }
    state = _make_state(contador)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    retefuente = next(i for i in impuestos if i["tipo_impuesto"] == "retefuente")
    assert Decimal(retefuente["valor_impuesto"]) == Decimal("60000.00")
    assert retefuente["cuenta_puc"] == "2365"


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_reteica_applied(mock_rag_cls, mock_llm_fn):
    """ReteICA = 0.69% applied, cuenta 2368."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    reteica = next(i for i in impuestos if i["tipo_impuesto"] == "reteica")
    assert Decimal(reteica["valor_impuesto"]) == Decimal("10350.00")
    assert reteica["cuenta_puc"] == "2368"


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_reteica_uses_custom_account_from_company_config(mock_rag_cls, mock_llm_fn):
    """ReteICA uses cuenta_ica_propio from company_config when provided."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    cfg = {
        "tasa_reteica": 0.0069,
        "tasa_retefuente_servicios": 0.11,
        "tasa_retefuente_bienes": 0.025,
        "tasa_retefuente_arrendamiento": 0.035,
        "tasa_iva_general": 0.19,
        "iva_responsable": True,
        "tasa_ica": 0.0069,
        "tasa_renta": 0.35,
        "cuenta_ica_propio": "2367",
    }
    state = _make_state(VALID_CONTADOR_OUTPUT, company_config=cfg)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    reteica = next(i for i in impuestos if i["tipo_impuesto"] == "reteica")
    assert reteica["cuenta_puc"] == "2367"

    enriched = result["tributario_output"]["asientos_enriquecidos"]
    cuentas = [a["cuenta_puc"] for a in enriched]
    assert "2367" in cuentas, (
        "Custom ReteICA account 2367 missing from enriched asientos"
    )


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_iva_calculated_when_not_in_asientos(mock_rag_cls, mock_llm_fn):
    """IVA 19% calculated when not present in contador asientos."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    iva = next(i for i in impuestos if i["tipo_impuesto"] == "IVA")
    assert Decimal(iva["valor_impuesto"]) == Decimal("285000.00")


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_iva_captured_from_asientos_not_doubled(mock_rag_cls, mock_llm_fn):
    """IVA from contador asientos is captured, not recalculated."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT_WITH_IVA)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    iva_entries = [i for i in impuestos if i["tipo_impuesto"] == "IVA"]
    assert len(iva_entries) == 1
    assert Decimal(iva_entries[0]["valor_impuesto"]) == Decimal("285000.00")

    # Should NOT add a new IVA entry to asientos_enriquecidos — the original from
    # contador already covers it. Check that the IVA sub-account (240802) is absent.
    enriquecidos = result["tributario_output"]["asientos_enriquecidos"]
    new_iva_entries = [a for a in enriquecidos if a.get("cuenta_puc") == "240802"]
    assert len(new_iva_entries) == 0, (
        "Tributario must not add IVA when already in asientos"
    )


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_retefuente_bienes_2_5_percent(mock_rag_cls, mock_llm_fn):
    """Retefuente = 2.5% for bienes declarantes (non-5xxx PUC), base 2,000,000.

    Base must exceed the 27-UVT minimum ($1,414,098 at UVT 2026) for the
    agent to actually withhold; otherwise retefuente is correctly zeroed.
    """
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT_BIENES)
    result = tributario_node(state)

    impuestos = result["tributario_output"]["impuestos"]
    retefuente = next(i for i in impuestos if i["tipo_impuesto"] == "retefuente")
    assert Decimal(retefuente["valor_impuesto"]) == Decimal("50000.00")


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_upstream_error_passthrough(mock_rag_cls, mock_llm_fn):
    """Node skips processing if upstream error is set."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT, error="Upstream failure")
    result = tributario_node(state)

    assert result["error"] == "Upstream failure"
    assert result.get("tributario_output") == {}
    mock_llm_fn.return_value.justify_tax_analysis.assert_not_called()


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_missing_contador_output_sets_error(mock_rag_cls, mock_llm_fn):
    """Missing contador_output results in an error being set."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(contador_output={})
    result = tributario_node(state)

    assert result["error"] is not None
    assert "contador_output" in result["error"].lower()


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_schema_valid(mock_rag_cls, mock_llm_fn):
    """tributario_output validates against TributarioOutput Pydantic schema."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    output = result["tributario_output"]
    parsed = TributarioOutput.model_validate(output)
    assert parsed.aplica_impuestos is True
    assert parsed.total_impuestos > 0


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_gemini_fallback_on_failure(mock_rag_cls, mock_llm_fn):
    """Node completes when LLMClient returns a static fallback response."""
    mock_rag = MagicMock()
    mock_rag.search_normativo.return_value = []
    mock_rag_cls.return_value = mock_rag

    # Simulate the built-in fallback path: justify_tax_analysis returns a static
    # TaxJustification (as LLMClient does internally when the API call fails).
    mock_gc = MagicMock()
    mock_gc.justify_tax_analysis.return_value = TaxJustification(
        referencias=["Art. 383 ET", "Art. 401 ET", "Art. 477 ET", "Decreto 2048/1992"],
        justificacion=(
            "Retenciones aplicadas según tasas vigentes ET. "
            "Respuesta de respaldo por error en la API."
        ),
        confirma_tasas=True,
    )
    mock_llm_fn.return_value = mock_gc

    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    # Should still complete — fallback kicks in
    assert result.get("error") is None
    assert result["tributario_output"].get("aplica_impuestos") is True
    assert "Art. 383 ET" in result["tributario_output"].get("referencias_legales", [])


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_rag_fallback_on_failure(mock_rag_cls, mock_llm_fn):
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
    mock_llm_fn.return_value = mock_gc

    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    assert result["tributario_output"] != {}


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_journal_entries_enriched_with_tax_accounts(mock_rag_cls, mock_llm_fn):
    """Enriched asientos contain tax liability accounts (2365, 2368, 240802)."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    enriquecidos = result["tributario_output"]["asientos_enriquecidos"]
    cuentas = [a["cuenta_puc"] for a in enriquecidos]

    assert "2365" in cuentas, "Retefuente account 2365 missing from enriched asientos"
    assert "2368" in cuentas, "ReteICA account 2368 missing from enriched asientos"
    assert "240802" in cuentas, "IVA account 240802 missing from enriched asientos"


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_referencias_legales_in_output(mock_rag_cls, mock_llm_fn):
    """Legal references from Gemini are stored in tributario_output."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    referencias = result["tributario_output"].get("referencias_legales", [])
    assert len(referencias) > 0
    assert any("Art. 383" in r for r in referencias)


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_total_impuestos_matches_sum(mock_rag_cls, mock_llm_fn):
    """total_impuestos equals the sum of individual impuesto values."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    output = result["tributario_output"]
    impuestos = output["impuestos"]
    calculated = sum(Decimal(i["valor_impuesto"]) for i in impuestos)
    stored = Decimal(output["total_impuestos"])
    assert calculated == stored


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_agent_log_entries_written(mock_rag_cls, mock_llm_fn):
    """node_start and node_complete are written to agent_log."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    events = [e["event"] for e in result["agent_log"]]
    assert "node_start" in events
    assert "node_complete" in events


@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_smoke_1500000_honorarios(mock_rag_cls, mock_llm_fn):
    """
    Smoke test: $1,500,000 honorarios (PUC 5110).
    Expected: retefuente=165,000 (11%), reteica=10,350 (0.69%), iva=285,000 (19%),
    total=460,350.
    """
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(VALID_CONTADOR_OUTPUT)
    result = tributario_node(state)

    output = result["tributario_output"]
    impuestos = {
        i["tipo_impuesto"]: Decimal(i["valor_impuesto"]) for i in output["impuestos"]
    }

    assert impuestos["retefuente"] == Decimal("165000.00")
    assert impuestos["reteica"] == Decimal("10350.00")
    assert impuestos["IVA"] == Decimal("285000.00")
    assert Decimal(output["total_impuestos"]) == Decimal("460350.00")


@patch("app.services.db_service.get_company_settings", return_value=None)
@patch("app.core.database.SessionLocal")
def test_process_mode_fails_when_company_settings_missing(
    mock_session_local, mock_get_settings
):
    """Process mode must fail fast when no company_settings row exists for NIT."""
    _ = mock_get_settings
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db

    state = _make_state(VALID_CONTADOR_OUTPUT)
    state["raw_transactions"] = [{"nit_receptor": "800999888"}]

    result = tributario_node(state)

    assert result.get("error") is not None
    assert "missing company tax settings" in result["error"].lower()
    assert "/api/v1/settings/company/800999888/setup" in result["error"]


@patch("app.services.db_service.get_company_settings")
@patch("app.core.database.SessionLocal")
@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_process_mode_uses_company_settings_when_present(
    mock_rag_cls,
    mock_llm_fn,
    mock_session_local,
    mock_get_settings,
):
    """Process mode should continue successfully when company settings exist."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)

    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    mock_get_settings.return_value = SimpleNamespace(
        tasa_retefuente_servicios=Decimal("0.110000"),
        tasa_retefuente_bienes=Decimal("0.030000"),
        tasa_retefuente_arrendamiento=Decimal("0.100000"),
        tasa_reteica=Decimal("0.006900"),
        tasa_iva_general=Decimal("0.190000"),
        iva_responsable=True,
        tasa_ica=Decimal("0.00690000"),
        tasa_renta=Decimal("0.350000"),
    )

    state = _make_state(VALID_CONTADOR_OUTPUT)
    state["raw_transactions"] = [{"nit_receptor": "800999888"}]

    result = tributario_node(state)

    assert result.get("error") is None
    assert result["tributario_output"] != {}


INCOME_CONTADOR_OUTPUT = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "factura",
    "descripcion_general": "Venta de servicios",
    "asientos": [
        {
            "cuenta_puc": "1110",
            "nombre_cuenta": "Bancos",
            "tipo_movimiento": "debito",
            "valor": 1000000,
            "descripcion": "Recaudo venta",
        },
        {
            "cuenta_puc": "4135",
            "nombre_cuenta": "Ingresos por Servicios",
            "tipo_movimiento": "credito",
            "valor": 1000000,
            "descripcion": "Venta servicios",
        },
    ],
    "total_debitos": 1000000,
    "total_creditos": 1000000,
}


@patch("app.agents.tributario_agent.get_rag_service")
@patch("app.agents.tributario_agent.get_llm_client")
def test_ica_not_applied_per_transaction(mock_llm_fn, mock_rag_cls):
    """ICA is never accrued per transaction (CPA review): it is a municipal tax on
    the company's own gross income, settled only in the ICA declaration. No 'ica'
    tax line nor 511505 'gasto ICA' should appear on any individual movement."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(INCOME_CONTADOR_OUTPUT)
    result = tributario_node(state)

    assert result.get("error") is None
    trib = result["tributario_output"]
    ica_entry = next(
        (i for i in trib["impuestos"] if i["tipo_impuesto"] == "ica"), None
    )
    assert ica_entry is None, "ICA must NOT be emitted per transaction"

    puc_codes = [a["cuenta_puc"] for a in trib["asientos_enriquecidos"]]
    assert "511505" not in puc_codes, (
        "Gasto ICA (511505) must NOT be injected per transaction"
    )


RECIBO_CAJA_CONTADOR_OUTPUT = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "recibo_caja",
    "descripcion_general": "Recibo de caja RC-001",
    "asientos": [
        {
            "cuenta_puc": "1110",
            "nombre_cuenta": "Bancos",
            "tipo_movimiento": "debito",
            "valor": 1000000,
            "descripcion": "Recaudo",
        },
        {
            "cuenta_puc": "130505",
            "nombre_cuenta": "Clientes",
            "tipo_movimiento": "credito",
            "valor": 1000000,
            "descripcion": "Cancelación factura",
        },
    ],
    "total_debitos": 1000000,
    "total_creditos": 1000000,
}


@patch("app.agents.tributario_agent.get_rag_service")
@patch("app.agents.tributario_agent.get_llm_client")
def test_recibo_caja_cobro_cartera_is_tax_neutral(mock_llm_fn, mock_rag_cls):
    """A recibo de caja cobro_cartera only collects an existing invoice — IVA and
    retenciones were booked on the original factura, so none are applied here."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(RECIBO_CAJA_CONTADOR_OUTPUT)
    state["document_classification"] = {"doc_type": "recibo_caja"}
    state["source_document"] = {"tipo_recibo": "cobro_cartera"}

    result = tributario_node(state)

    assert result.get("error") is None
    trib = result["tributario_output"]
    assert trib["aplica_impuestos"] is False
    assert trib["impuestos"] == []
    puc_codes = [a["cuenta_puc"] for a in trib["asientos_enriquecidos"]]
    assert "240805" not in puc_codes  # no IVA generado
    assert "135515" not in puc_codes  # no retefuente recibida


@patch("app.agents.tributario_agent.get_rag_service")
@patch("app.agents.tributario_agent.get_llm_client")
def test_recibo_caja_venta_directa_applies_sale_taxes(mock_llm_fn, mock_rag_cls):
    """A recibo de caja venta_directa is a sale without a prior invoice → seller-side
    taxes (IVA generado) apply."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(INCOME_CONTADOR_OUTPUT)
    state["document_classification"] = {"doc_type": "recibo_caja"}
    state["source_document"] = {"tipo_recibo": "venta_directa"}

    result = tributario_node(state)

    assert result.get("error") is None
    trib = result["tributario_output"]
    assert trib["aplica_impuestos"] is True
    iva = next(
        (i for i in trib["impuestos"] if i["tipo_impuesto"].lower() == "iva"), None
    )
    assert iva is not None and iva["cuenta_puc"] == "240805"  # IVA generado (venta)


DOC_SOPORTE_CONTADOR_OUTPUT = {
    "fecha_registro": "2026-03-07",
    "tipo_documento": "documento_soporte",
    "descripcion_general": "Documento soporte servicios",
    "asientos": [
        {
            "cuenta_puc": "511525",
            "nombre_cuenta": "Servicios técnicos",
            "tipo_movimiento": "debito",
            "valor": 1000000,
            "descripcion": "Servicio",
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
}


@patch("app.agents.tributario_agent.get_rag_service")
@patch("app.agents.tributario_agent.get_llm_client")
def test_documento_soporte_no_iva_descontable(mock_llm_fn, mock_rag_cls):
    """Documento soporte providers are not IVA-responsible → no IVA descontable
    (240802), even if the doc shows IVA; the output flags it for review."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)
    state = _make_state(DOC_SOPORTE_CONTADOR_OUTPUT)
    state["document_classification"] = {"doc_type": "documento_soporte"}
    state["source_document"] = {"totales": {"total_iva": 190000}}

    result = tributario_node(state)

    assert result.get("error") is None
    trib = result["tributario_output"]
    iva = next(
        (i for i in trib["impuestos"] if i["tipo_impuesto"].lower() == "iva"), None
    )
    assert iva is None, "documento_soporte must not produce IVA descontable"
    puc_codes = [a["cuenta_puc"] for a in trib["asientos_enriquecidos"]]
    assert "240802" not in puc_codes
    assert "REVISAR" in trib["observaciones"]


@patch("app.services.db_service.get_company_settings")
@patch("app.core.database.SessionLocal")
@patch("app.agents.tributario_agent.get_llm_client")
@patch("app.agents.tributario_agent.get_rag_service")
def test_process_mode_without_taxes_does_not_crash(
    mock_rag_cls,
    mock_llm_fn,
    mock_session_local,
    mock_get_settings,
):
    """When no tax applies, tributario must keep total_impuestos=0 without quantize errors."""
    _mock_llm_and_rag(mock_rag_cls, mock_llm_fn)

    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    mock_get_settings.return_value = SimpleNamespace(
        tasa_retefuente_servicios=Decimal("0.000000"),
        tasa_retefuente_bienes=Decimal("0.000000"),
        tasa_retefuente_arrendamiento=Decimal("0.000000"),
        tasa_reteica=Decimal("0.000000"),
        tasa_iva_general=Decimal("0.000000"),
        iva_responsable=False,
        tasa_ica=Decimal("0.006900"),
        tasa_renta=Decimal("0.350000"),
    )

    # Use a configurable servicios account (511525): honorarios (5110) is a
    # statutory 11% and cannot be zeroed via company_config, so it would not
    # exercise the no-tax path this test guards.
    contador = {
        **VALID_CONTADOR_OUTPUT,
        "asientos": [
            {
                "cuenta_puc": "511525",
                "nombre_cuenta": "Servicios técnicos",
                "tipo_movimiento": "debito",
                "valor": 1500000,
                "descripcion": "Servicio técnico",
            },
            {
                "cuenta_puc": "1110",
                "nombre_cuenta": "Bancos",
                "tipo_movimiento": "credito",
                "valor": 1500000,
                "descripcion": "Pago",
            },
        ],
    }
    state = _make_state(contador)
    state["raw_transactions"] = [{"nit_receptor": "800999888"}]

    result = tributario_node(state)

    assert result.get("error") is None
    assert result["tributario_output"].get("aplica_impuestos") is False
    assert Decimal(str(result["tributario_output"].get("total_impuestos"))) == Decimal(
        "0"
    )
