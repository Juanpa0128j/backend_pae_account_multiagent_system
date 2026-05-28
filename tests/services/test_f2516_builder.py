"""Unit tests for the auto-populated _build_f2516 builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.tax_declaration_service import generate_declaration_draft


def _make_settings():
    s = MagicMock()
    s.nit = "900123456"
    s.tasa_renta = Decimal("0.35")
    return s


def _make_ledger():
    """Sample ledger spanning relevant PUC classes."""
    return [
        {
            "account": "1105",
            "name": "Caja",
            "total_debit": 500_000.0,
            "total_credit": 0.0,
            "net_balance": 500_000.0,
        },
        {
            "account": "1305",
            "name": "Clientes",
            "total_debit": 200_000.0,
            "total_credit": 0.0,
            "net_balance": 200_000.0,
        },
        {
            "account": "1435",
            "name": "Inventario",
            "total_debit": 300_000.0,
            "total_credit": 0.0,
            "net_balance": 300_000.0,
        },
        {
            "account": "1520",
            "name": "PPE",
            "total_debit": 1_000_000.0,
            "total_credit": 0.0,
            "net_balance": 1_000_000.0,
        },
        {
            "account": "2105",
            "name": "Bancos",
            "total_debit": 0.0,
            "total_credit": 400_000.0,
            "net_balance": -400_000.0,
        },
        {
            "account": "2365",
            "name": "Retefuente x pagar",
            "total_debit": 0.0,
            "total_credit": 50_000.0,
            "net_balance": -50_000.0,
        },
        {
            "account": "4135",
            "name": "Ingresos",
            "total_debit": 0.0,
            "total_credit": 2_000_000.0,
            "net_balance": -2_000_000.0,
        },
        {
            "account": "6135",
            "name": "Costo ventas",
            "total_debit": 800_000.0,
            "total_credit": 0.0,
            "net_balance": 800_000.0,
        },
        {
            "account": "5110",
            "name": "Gastos op",
            "total_debit": 300_000.0,
            "total_credit": 0.0,
            "net_balance": 300_000.0,
        },
    ]


def _make_ajuste(
    seccion: str, concepto: str, contable: float, fiscal: float, tipo: str
):
    a = MagicMock()
    a.seccion = seccion
    a.concepto = concepto
    a.valor_contable = Decimal(str(contable))
    a.valor_fiscal = Decimal(str(fiscal))
    a.tipo_diferencia = tipo
    return a


def _generate(settings, ledger, ajustes=None, year: int = 2026):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = settings
    with (
        patch(
            "app.services.tax_declaration_service.db_service.get_general_ledger",
            return_value=ledger,
        ),
        patch(
            "app.services.tax_declaration_service.db_service.list_ajustes_fiscales",
            return_value=ajustes or [],
        ),
    ):
        return generate_declaration_draft(
            db, "900123456", "F2516", date(year, 1, 1), date(year, 12, 31)
        )


def _by_renglon(draft):
    return {f["renglon"]: f for f in draft.fields_json}


class TestF2516ESFNoAjustes:
    def test_total_activos_contables_from_clase_1(self):
        draft = _generate(_make_settings(), _make_ledger())
        fields = _by_renglon(draft)
        # 500k + 200k + 300k + 1_000_000 = 2_000_000
        assert fields["190"]["value"] == pytest.approx(2_000_000.0)

    def test_total_activos_fiscales_equal_contables_without_ajustes(self):
        draft = _generate(_make_settings(), _make_ledger())
        fields = _by_renglon(draft)
        assert fields["199"]["value"] == pytest.approx(fields["190"]["value"])
        assert fields["191"]["value"] == pytest.approx(0.0)
        # 191 requires review when no ajustes
        assert fields["191"]["requires_review"] is True

    def test_total_pasivos_contables_from_clase_2(self):
        draft = _generate(_make_settings(), _make_ledger())
        fields = _by_renglon(draft)
        assert fields["240"]["value"] == pytest.approx(450_000.0)

    def test_patrimonio_fiscal_equals_activos_minus_pasivos(self):
        draft = _generate(_make_settings(), _make_ledger())
        fields = _by_renglon(draft)
        assert fields["290"]["value"] == pytest.approx(
            fields["199"]["value"] - fields["249"]["value"]
        )


class TestF2516ESFConAjustes:
    def test_ajuste_activo_se_suma_a_fiscal(self):
        ajustes = [
            _make_ajuste(
                "ESF_ACTIVO",
                "depreciacion_acelerada",
                100_000.0,
                150_000.0,
                "temporaria_imponible",
            )
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        assert fields["191"]["value"] == pytest.approx(50_000.0)
        assert fields["191"]["source"] == "ajustes_fiscales"
        assert fields["191"]["requires_review"] is False
        assert fields["199"]["value"] == pytest.approx(
            fields["190"]["value"] + 50_000.0
        )

    def test_ajuste_pasivo_modifica_total_pasivos_fiscales(self):
        ajustes = [
            _make_ajuste(
                "ESF_PASIVO",
                "provision_no_aceptada",
                100_000.0,
                0.0,
                "permanente",
            )
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        assert fields["241"]["value"] == pytest.approx(-100_000.0)
        assert fields["249"]["value"] == pytest.approx(
            fields["240"]["value"] - 100_000.0
        )


class TestF2516ERI:
    def test_total_ingresos_fiscales_with_ajuste(self):
        ajustes = [
            _make_ajuste(
                "ERI_INGRESO",
                "ingresos_no_constitutivos",
                100_000.0,
                0.0,
                "permanente",
            )
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        assert fields["329"]["value"] == pytest.approx(2_000_000.0 - 100_000.0)

    def test_total_costos_fiscales_with_ajuste(self):
        ajustes = [
            _make_ajuste(
                "ERI_COSTO",
                "costos_no_deducibles",
                50_000.0,
                0.0,
                "permanente",
            )
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        # 800_000 + (0 - 50_000) = 750_000
        assert fields["419"]["value"] == pytest.approx(750_000.0)

    def test_renta_liquida_fiscal_correcta_con_ajustes(self):
        # Ingresos 2M; costos contables 800k; gastos 300k
        # Ajuste gasto 200k no deducible (Art. 107 ET) → reduce gastos fiscales → +200k RLO
        ajustes = [
            _make_ajuste(
                "ERI_GASTO",
                "gastos_no_deducibles_art_107",
                200_000.0,
                0.0,
                "permanente",
            )
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        # RLF = 2_000_000 - 800_000 - (300_000 - 200_000) = 2_000_000 - 800_000 - 100_000 = 1_100_000
        assert fields["600"]["value"] == pytest.approx(1_100_000.0)

    def test_renglon_4_mirrors_600_for_f110_compat(self):
        draft = _generate(_make_settings(), _make_ledger())
        fields = _by_renglon(draft)
        assert fields["4"]["value"] == pytest.approx(fields["600"]["value"])


class TestF2516ImpuestoDiferido:
    def test_impuesto_diferido_neto_35_pct_sobre_temporarias(self):
        # temp_imp 100, temp_ded 40 → neto = 60 × 0.35 = 21
        ajustes = [
            _make_ajuste("ESF_ACTIVO", "x", 0.0, 100.0, "temporaria_imponible"),
            _make_ajuste("ESF_PASIVO", "y", 100.0, 60.0, "temporaria_deducible"),
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        # temp_imp delta = +100; temp_ded delta = -40 → temp_ded sum = -40
        # impuesto = (100 - (-40)) * 0.35 = 140 * 0.35 = 49
        assert fields["730"]["value"] == pytest.approx(49.0)

    def test_diferencias_permanentes_aggregate(self):
        ajustes = [
            _make_ajuste("ERI_GASTO", "a", 100.0, 0.0, "permanente"),
            _make_ajuste("ERI_GASTO", "b", 50.0, 0.0, "permanente"),
        ]
        draft = _generate(_make_settings(), _make_ledger(), ajustes=ajustes)
        fields = _by_renglon(draft)
        # delta per row = -100 and -50 → sum = -150
        assert fields["700"]["value"] == pytest.approx(-150.0)


class TestF2516EmptyLedger:
    def test_no_ledger_all_zeros(self):
        draft = _generate(_make_settings(), [])
        fields = _by_renglon(draft)
        for renglon in ("190", "199", "240", "249", "290", "329", "419", "529", "600"):
            assert fields[renglon]["value"] == pytest.approx(0.0)

    def test_warning_emitted_when_no_ajustes(self):
        draft = _generate(_make_settings(), _make_ledger())
        warn_fields = [w["field"] for w in draft.warnings_json]
        assert "ajustes_fiscales" in warn_fields

    def test_warning_emitted_when_no_ledger(self):
        draft = _generate(_make_settings(), [])
        warn_fields = [w["field"] for w in draft.warnings_json]
        assert "general" in warn_fields
