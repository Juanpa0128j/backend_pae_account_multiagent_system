import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


def test_build_first_level_skips_existing_types():
    """If all statement types already exist for company+period, none are re-created."""
    from app.services import financial_statement_service as fss

    db = MagicMock()

    with (
        patch.object(fss, "_first_level_type_exists", return_value=True),
        patch.object(fss.db_service, "create_financial_statement") as mock_create,
    ):
        result = fss.build_first_level_from_journal_entries(
            db,
            company_nit="800999888",
            period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    mock_create.assert_not_called()
    assert result["skipped"] == 4


def test_build_first_level_creates_when_missing():
    """When no statements exist, all 4 types should be created."""
    from app.services import financial_statement_service as fss

    db = MagicMock()

    mock_stmt = MagicMock()
    mock_stmt.id = "stmt-id-1"
    mock_ingest = MagicMock()
    mock_ingest.id = "ingest-id-1"

    with (
        patch.object(fss, "_first_level_type_exists", return_value=False),
        patch.object(fss, "_create_derivation_ingest_job", return_value=mock_ingest),
        patch.object(
            fss.db_service, "get_balance_sheet", return_value={"total_activos": 100}
        ),
        patch.object(fss.db_service, "get_pnl", return_value={"utilidad_neta": 50}),
        patch.object(fss.db_service, "get_general_ledger", return_value=[]),
        patch.object(fss.db_service, "get_journal_entry_lines", return_value=[]),
        patch.object(
            fss.db_service, "create_financial_statement", return_value=mock_stmt
        ) as mock_create,
    ):
        result = fss.build_first_level_from_journal_entries(
            db,
            company_nit="800999888",
            period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    assert mock_create.call_count == 4
    assert result["skipped"] == 0
    assert len(result["created"]) == 4


# ─── v3 derivation helpers ────────────────────────────────────────────────────


def _bg_with(
    activos_corrientes=None,
    pasivos_corrientes=None,
    patrimonio=None,
    activos_no_corrientes=None,
    pasivos_no_corrientes=None,
    accounts=None,
    total_patrimonio=0,
):
    return {
        "total_patrimonio": total_patrimonio,
        "activos_corrientes": activos_corrientes or {},
        "activos_no_corrientes": activos_no_corrientes or {},
        "pasivos_corrientes": pasivos_corrientes or {},
        "pasivos_no_corrientes": pasivos_no_corrientes or {},
        "patrimonio": patrimonio or {},
        "accounts": accounts or [],
    }


def test_cash_flow_includes_working_capital_and_depreciation():
    """Working capital deltas + depreciation add-back must be applied."""
    from app.services.financial_statement_service import _compute_cash_flow_indirect

    bg = _bg_with(
        activos_corrientes={
            "efectivo_equivalentes": 1_500,
            "cuentas_por_cobrar_comerciales": 800,
            "inventarios": 200,
        },
        pasivos_corrientes={
            "cuentas_por_pagar_comerciales": 400,
            "obligaciones_laborales": 100,
        },
        accounts=[{"cuenta_puc": "159205", "saldo": "300"}],
    )
    prior_bg = _bg_with(
        activos_corrientes={
            "efectivo_equivalentes": 1_000,
            "cuentas_por_cobrar_comerciales": 600,
            "inventarios": 100,
        },
        pasivos_corrientes={
            "cuentas_por_pagar_comerciales": 350,
            "obligaciones_laborales": 80,
        },
        accounts=[{"cuenta_puc": "159205", "saldo": "200"}],
    )
    er = {"utilidad_neta": 400, "impuesto_renta": 0}

    out = _compute_cash_flow_indirect(
        company_nit="800999888",
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        bg_data=bg,
        prior_bg_data=prior_bg,
        er_data=er,
        la_data={"lines": []},
    )

    # flujo_op = 400 (utilidad) + 100 (dep) - 200 (Δcxc) - 100 (Δinv) + Δ op_liab (50 cxp + 20 oblab) = 270
    assert out["flujo_neto_operacion"] == 270.0
    assert out["informacion_adicional"]["adjustments"]["depreciacion_periodo"] == 100.0
    assert (
        out["informacion_adicional"]["adjustments"]["delta_cuentas_por_cobrar"] == 200.0
    )
    assert (
        out["informacion_adicional"]["adjustments"]["delta_pasivos_operacionales"]
        == 70.0
    )


def test_equity_changes_splits_components():
    """Cambios en patrimonio must emit one componente per non-zero sub-account."""
    from app.services.financial_statement_service import _compute_equity_changes

    bg = _bg_with(
        total_patrimonio=1500,
        patrimonio={
            "capital_social": 1000,
            "reservas": 200,
            "resultados_del_ejercicio": 100,
            "resultados_acumulados": 200,
            "otro_resultado_integral": 0,
        },
    )
    prior_bg = _bg_with(
        patrimonio={
            "capital_social": 1000,
            "reservas": 200,
            "resultados_del_ejercicio": 0,
            "resultados_acumulados": 150,
            "otro_resultado_integral": 0,
        },
    )
    out = _compute_equity_changes(
        company_nit="800999888",
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        bg_data=bg,
        prior_bg_data=prior_bg,
        er_data={"utilidad_neta": 100},
        la_data={"lines": []},
    )

    componentes = {c["concepto_patrimonio"]: c for c in out["componentes"]}
    assert "otro_resultado_integral" not in componentes  # ambos saldos en 0 → skip
    assert {
        "capital_social",
        "reservas",
        "resultados_del_ejercicio",
        "resultados_acumulados",
    } <= componentes.keys()
    movs = componentes["resultados_del_ejercicio"]["movimientos"]
    assert any(
        m["concepto"] == "utilidad_neta_del_periodo" and m["valor"] == 100.0
        for m in movs
    )
    assert out["total_patrimonio_fin"] == 1500.0


def test_notes_skip_empty_classes():
    """A class with no accounts in BG/ER must produce no note."""
    from app.services.financial_statement_service import _compute_notes

    bg = _bg_with(
        accounts=[
            {"cuenta_puc": "1105", "nombre": "Caja", "saldo": "500"},
            {"cuenta_puc": "1110", "nombre": "Bancos", "saldo": "1000"},
        ]
    )
    er = {"accounts": [{"cuenta_puc": "4135", "nombre": "Ventas", "saldo": "9000"}]}

    out = _compute_notes(
        company_nit="800999888",
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        bg_data=bg,
        er_data=er,
        la_data={"lines": []},
    )

    nums = {n["numero_nota"] for n in out["notas"]}
    assert {"1", "2", "3", "4", "11"} <= nums
    assert "6" not in nums  # PPE no tiene cuentas → skip
    cash_note = next(n for n in out["notas"] if n["numero_nota"] == "4")
    conceptos = [c["concepto"] for c in cash_note["cifras_relevantes"]]
    assert any("Caja" in c for c in conceptos)
    assert any("Bancos" in c for c in conceptos)


def test_derive_raises_when_prior_balance_missing():
    """The pipeline must raise BusinessRuleError when no prior BG exists."""
    from app.services import financial_statement_service as fss

    fake_src = MagicMock()
    fake_src.data = {}
    with (
        patch.object(fss, "_load_prior_balance", return_value=None),
        patch.object(fss, "_first_level_type_exists", return_value=False),
        patch.object(
            fss.db_service, "get_financial_statements", return_value=[fake_src]
        ),
        patch.object(fss, "SessionLocal", return_value=MagicMock()),
    ):
        try:
            fss.derive_financial_statements(
                company_nit="800999888",
                period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
            )
        except fss.BusinessRuleError as exc:
            assert "período anterior" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("Expected BusinessRuleError when prior BG is missing")


# ─── v4 leaf-based extraction ─────────────────────────────────────────────────


def test_leaf_accounts_drops_hallucinated_codes():
    """LLM sometimes hallucinates the saldo as cuenta_puc on TOTAL lines
    (e.g. '169098236'). Codes > 8 digits or non-numeric must be discarded."""
    from app.services.financial_statement_service import _leaf_accounts

    accs = [
        {"cuenta_puc": "1105", "saldo": "1000"},
        {"cuenta_puc": "169098236", "saldo": "169098236", "nombre": "TOTAL PASIVO"},
        {"cuenta_puc": "TOTAL", "saldo": "9999"},
        {"cuenta_puc": "", "saldo": "8888"},
    ]
    leaves = _leaf_accounts(accs)
    codes = {a["cuenta_puc"] for a in leaves}
    assert codes == {"1105"}, codes


def test_leaf_accounts_drops_aggregates():
    """Hierarchical PUC rows must collapse to leaves only — class/group/account
    aggregates are dropped so prefix-sums don't double-count."""
    from app.services.financial_statement_service import _leaf_accounts, _sum_leaves

    accounts = [
        {"cuenta_puc": "11", "nombre": "DISPONIBLE", "saldo": "92966415.31"},  # class
        {"cuenta_puc": "1120", "nombre": "CTA AHORRO", "saldo": "20552619.31"},  # group
        {"cuenta_puc": "112005", "nombre": "BANCOS", "saldo": "20552619.31"},  # account
        {
            "cuenta_puc": "11200501",
            "nombre": "Bancolombia",
            "saldo": "20552619.31",
        },  # leaf
        {"cuenta_puc": "1125", "nombre": "FONDOS", "saldo": "72413796"},  # group
        {"cuenta_puc": "112501", "nombre": "Fiducuenta", "saldo": "72413796"},  # leaf
    ]
    leaves = _leaf_accounts(accounts)
    leaf_codes = {a["cuenta_puc"] for a in leaves}
    assert leaf_codes == {"11200501", "112501"}, leaf_codes
    # Class 11 prefix sum over leaves must NOT double-count.
    total = _sum_leaves(leaves, "11")
    assert float(total) == pytest.approx(20552619.31 + 72413796)


def test_cash_flow_v4_handles_hierarchical_extraction():
    """When the LLM emits hierarchical rows for the same account at multiple
    levels, v4 must dedupe via _leaf_accounts so deltas are correct.
    Regression for the Pruebilla scenario observed in production."""
    from app.services.financial_statement_service import _compute_cash_flow_indirect

    # Jan-26 BG: hierarchical extraction (class + group + account + sub-account).
    bg = {
        "total_patrimonio": 0,
        "activos_corrientes": {},
        "activos_no_corrientes": {},
        "pasivos_corrientes": {},
        "pasivos_no_corrientes": {},
        "patrimonio": {},
        "accounts": [
            {"cuenta_puc": "11", "saldo": "1500"},
            {"cuenta_puc": "1105", "saldo": "1500"},
            {"cuenta_puc": "110505", "saldo": "1500"},  # leaf
            {"cuenta_puc": "159205", "saldo": "-300"},  # leaf — dep acumulada
        ],
    }
    # Dec-25 BG: leaves only.
    prior_bg = {
        "total_patrimonio": 0,
        "activos_corrientes": {},
        "activos_no_corrientes": {},
        "pasivos_corrientes": {},
        "pasivos_no_corrientes": {},
        "patrimonio": {},
        "accounts": [
            {"cuenta_puc": "110505", "saldo": "1000"},
            {"cuenta_puc": "159205", "saldo": "-200"},
        ],
    }
    er = {"utilidad_neta": 400, "impuesto_renta": 0}

    out = _compute_cash_flow_indirect(
        company_nit="800999888",
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        bg_data=bg,
        prior_bg_data=prior_bg,
        er_data=er,
        la_data={"lines": []},
    )

    # Efectivo fin must be 1500 (the leaf), not 4500 (sum of all 11* levels).
    assert out["efectivo_fin_periodo"] == 1500.0
    assert out["efectivo_inicio_periodo"] == 1000.0
    # Depreciation = abs(-300 - (-200)) = 100.
    assert out["informacion_adicional"]["adjustments"]["depreciacion_periodo"] == 100.0
    # rule_version must be v4.
    assert out["informacion_adicional"]["rule_version"] == "v4"
