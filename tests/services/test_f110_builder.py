"""
Unit tests for the refactored _build_f110 F110 draft builder.

Tests cover:
- All auto-calculated renglones
- F2516 integration (source switches to f2516 when reviewed F2516 exists)
- Pérdidas fiscales carry-forward
- Anticipo año siguiente calculation
- Descuentos tributarios itemization
- Warnings generated
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.tax_declaration_service import generate_declaration_draft

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    s = MagicMock()
    s.nit = "900123456"
    s.ciudad = "Bogotá"
    s.codigo_ciiu = "6201"
    s.iva_responsable = True
    s.tasa_renta = Decimal("0.35")
    s.tasa_ica = Decimal("0.00690")
    s.tasa_iva_general = Decimal("0.19")
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_ledger(
    ingresos: float = 2_000_000,
    costos: float = 500_000,
    gastos: float = 300_000,
    ica_511505: float = 20_000,
    retenciones: float = 40_000,
):
    ledger = [
        {
            "account": "4135",
            "name": "Ingresos servicios",
            "total_debit": 0.0,
            "total_credit": ingresos,
            "net_balance": -ingresos,
        },
        {
            "account": "6135",
            "name": "Costo servicios",
            "total_debit": costos,
            "total_credit": 0.0,
            "net_balance": costos,
        },
        {
            "account": "5110",
            "name": "Gastos operacionales",
            "total_debit": gastos,
            "total_credit": 0.0,
            "net_balance": gastos,
        },
        {
            "account": "511505",
            "name": "ICA administración",
            "total_debit": ica_511505,
            "total_credit": 0.0,
            "net_balance": ica_511505,
        },
        {
            "account": "521505",
            "name": "ICA ventas",
            "total_debit": 0.0,
            "total_credit": 0.0,
            "net_balance": 0.0,
        },
        {
            "account": "135518",
            "name": "Retefte recibida",
            "total_debit": retenciones,
            "total_credit": 0.0,
            "net_balance": retenciones,
        },
        {
            "account": "1105",
            "name": "Caja",
            "total_debit": 800_000.0,
            "total_credit": 200_000.0,
            "net_balance": 600_000.0,
        },
        {
            "account": "2105",
            "name": "Obligaciones financieras",
            "total_debit": 0.0,
            "total_credit": 300_000.0,
            "net_balance": -300_000.0,
        },
    ]
    return ledger


def _mock_db_no_f2516_no_perdidas(settings, year: int = 2026):
    """DB mock: reviewed F2516 present (needed for generate_declaration_draft gate),
    but get_latest_f2516_reviewed and perdidas return None/0."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = settings
    # F2516 prerequisite check (order_by().first())
    f2516_gate = MagicMock()
    f2516_gate.status = "reviewed"
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = f2516_gate
    return db


def _generate_f110(settings, ledger, extra_patches=None, year: int = 2026):
    db = _mock_db_no_f2516_no_perdidas(settings, year)
    patches = {
        "app.services.db_service.get_perdidas_disponibles": MagicMock(return_value=[]),
        "app.services.db_service.sum_perdidas_disponibles": MagicMock(
            return_value=Decimal("0")
        ),
        "app.services.db_service.sum_retenciones_anio": MagicMock(
            return_value=Decimal("40000")
        ),
        "app.services.db_service.get_latest_f2516_reviewed": MagicMock(
            return_value=None
        ),
    }
    if extra_patches:
        patches.update(extra_patches)

    with (
        patch(
            "app.services.tax_declaration_service.db_service.get_general_ledger",
            return_value=ledger,
        ),
        patch(
            "app.services.db_service.get_perdidas_disponibles",
            patches["app.services.db_service.get_perdidas_disponibles"],
        ),
        patch(
            "app.services.db_service.sum_perdidas_disponibles",
            patches["app.services.db_service.sum_perdidas_disponibles"],
        ),
        patch(
            "app.services.db_service.sum_retenciones_anio",
            patches["app.services.db_service.sum_retenciones_anio"],
        ),
        patch(
            "app.services.db_service.get_latest_f2516_reviewed",
            patches["app.services.db_service.get_latest_f2516_reviewed"],
        ),
    ):
        draft = generate_declaration_draft(
            db, "900123456", "F110", date(year, 1, 1), date(year, 12, 31)
        )
    return draft


# ---------------------------------------------------------------------------
# Basic renglones
# ---------------------------------------------------------------------------


class TestF110BasicRenglones:
    def test_renta_bruta_from_clase_4(self):
        settings = _make_settings()
        ledger = _make_ledger(ingresos=2_000_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["40"]["value"] == pytest.approx(2_000_000.0)
        assert fields["40"]["source"] == "clase_4_puc"

    def test_costos_from_clase_6(self):
        settings = _make_settings()
        ledger = _make_ledger(costos=500_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["52"]["value"] == pytest.approx(500_000.0)

    def test_gastos_from_clase_5(self):
        # ica_511505=0 to isolate gastos from 511505 contributions
        settings = _make_settings()
        ledger = _make_ledger(gastos=300_000, ica_511505=0)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["60"]["value"] == pytest.approx(300_000.0)

    def test_renta_liquida_ordinaria_computed(self):
        # ica_511505=0 so class-5 sum = gastos only
        # RLO = 2_000_000 - 500_000 - 300_000 = 1_200_000
        settings = _make_settings()
        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["f110_renta_liquida_ordinaria"]["value"] == pytest.approx(
            1_200_000.0
        )
        assert fields["f110_renta_liquida_ordinaria"]["source"] == "journal"

    def test_renta_liquida_gravable_clamped_at_zero_when_loss(self):
        # Ingresos < costos+gastos → RLO negative → clamped at 0
        settings = _make_settings()
        ledger = _make_ledger(
            ingresos=100_000, costos=300_000, gastos=200_000, ica_511505=0
        )
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["72"]["value"] == pytest.approx(0.0)

    def test_impuesto_basico_uses_tasa_renta(self):
        # RLG = 1_200_000, tasa = 35% → 420_000
        # ica_511505=0 → descuento_ica = 0 → impuesto_neto = impuesto_basico
        settings = _make_settings(tasa_renta=Decimal("0.35"))
        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["80"]["value"] == pytest.approx(420_000.0)

    def test_ica_deducible_field_present(self):
        settings = _make_settings()
        ledger = _make_ledger(ica_511505=20_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["63"]["value"] == pytest.approx(20_000.0)

    def test_activos_from_clase_1(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        # 135518(40k) + 1105(800k) = 840_000
        assert fields["26"]["value"] == pytest.approx(840_000.0)

    def test_retenciones_from_db(self):
        settings = _make_settings()
        ledger = _make_ledger(retenciones=0)
        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.sum_retenciones_anio": MagicMock(
                    return_value=Decimal("50000")
                ),
            },
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["92"]["value"] == pytest.approx(50_000.0)


# ---------------------------------------------------------------------------
# F2516 integration
# ---------------------------------------------------------------------------


class TestF110WithF2516:
    def test_rlo_from_f2516_when_reviewed(self):
        settings = _make_settings()
        ledger = _make_ledger(ingresos=2_000_000, costos=500_000, gastos=300_000)

        f2516_mock = MagicMock()
        f2516_mock.fields_json = [
            {
                "renglon": "4",
                "value": 999_000.0,
                "label": "Renta líquida fiscal conciliada",
            },
        ]

        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.get_latest_f2516_reviewed": MagicMock(
                    return_value=f2516_mock
                ),
            },
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["f110_renta_liquida_ordinaria"]["value"] == pytest.approx(
            999_000.0
        )
        assert fields["f110_renta_liquida_ordinaria"]["source"] == "f2516"
        assert fields["f110_renta_liquida_ordinaria"]["requires_review"] is False

    def test_rlo_from_journal_when_no_f2516(self):
        settings = _make_settings()
        ledger = _make_ledger(ingresos=2_000_000, costos=500_000, gastos=300_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["f110_renta_liquida_ordinaria"]["source"] == "journal"


# ---------------------------------------------------------------------------
# Pérdidas fiscales
# ---------------------------------------------------------------------------


class TestF110WithPerdidas:
    def test_perdidas_field_shows_sum(self):
        settings = _make_settings()
        ledger = _make_ledger(ingresos=2_000_000, costos=500_000, gastos=300_000)
        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.sum_perdidas_disponibles": MagicMock(
                    return_value=Decimal("400000")
                ),
            },
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["f110_perdidas_compensar"]["value"] == pytest.approx(400_000.0)
        assert fields["f110_perdidas_compensar"]["requires_review"] is True

    def test_renta_liquida_gravable_reduced_by_perdidas(self):
        # ica_511505=0 → class-5 debits = gastos only = 300_000
        # RLO = 2_000_000 - 500_000 - 300_000 = 1_200_000
        # perdidas = 400_000 → RLG = max(0, 1_200_000 - 400_000) = 800_000
        settings = _make_settings()
        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )
        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.sum_perdidas_disponibles": MagicMock(
                    return_value=Decimal("400000")
                ),
            },
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["72"]["value"] == pytest.approx(800_000.0)

    def test_no_perdidas_when_none_available(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["f110_perdidas_compensar"]["value"] == pytest.approx(0.0)
        assert fields["f110_perdidas_compensar"]["requires_review"] is False


# ---------------------------------------------------------------------------
# Descuentos tributarios
# ---------------------------------------------------------------------------


class TestF110Descuentos:
    def test_ica_not_a_descuento_86_ica_absent(self):
        """Ley 2277/2022 Art. 19: ICA is now 100% deducción, not descuento.
        86_ica must NOT appear in F110 output since AG 2023."""
        settings = _make_settings()
        ledger = _make_ledger(ica_511505=20_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert "86_ica" not in fields, (
            "86_ica debe estar ausente: Ley 2277/2022 Art. 19 convirtió ICA a "
            "deducción 100% (Art. 115 ET), no descuento tributario"
        )

    def test_ica_flows_as_deduccion_via_renglon_63(self):
        """ICA still visible via renglon 63 as informational deducción line."""
        settings = _make_settings()
        ledger = _make_ledger(ica_511505=20_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert "63" in fields
        assert fields["63"]["value"] == pytest.approx(20_000.0)
        assert fields["63"]["source"] == "cuentas_511505_521505"

    def test_descuentos_itemized_fields_present(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        for renglon in [
            "86_donaciones",
            "86_iva_capital",
            "86_educacion",
            "86_otros",
        ]:
            assert renglon in fields, f"Missing descuento field: {renglon}"

    def test_descuentos_manual_fields_require_review(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        for renglon in ["86_donaciones", "86_iva_capital", "86_educacion", "86_otros"]:
            assert fields[renglon]["requires_review"] is True

    def test_total_descuentos_86_is_zero_without_other_descuentos(self):
        """Without 86_ica, total descuentos starts at 0 (others manual/zero)."""
        settings = _make_settings()
        ledger = _make_ledger(ica_511505=20_000)
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        # ICA is no longer a descuento per Ley 2277/2022 Art. 19
        assert fields["86"]["value"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Anticipo año siguiente
# ---------------------------------------------------------------------------


class TestF110Anticipo:
    def test_anticipo_calculated_from_impuesto_neto(self):
        # RLG = 1_200_000, tasa = 35% → impuesto_basico = 420_000
        # No ICA descuento (Ley 2277/2022 Art. 19) → impuesto_neto = 420_000
        # retenciones_año_anterior = 0 → anticipo = 420_000 × 0.75 = 315_000
        settings = _make_settings()
        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=20_000
        )
        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.sum_retenciones_anio": MagicMock(
                    return_value=Decimal("0")
                ),
            },
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        impuesto_neto = fields["88"]["value"]
        anticipo = fields["95"]["value"]
        assert anticipo == pytest.approx(impuesto_neto * 0.75, rel=1e-3)

    def test_anticipo_requires_review(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["95"]["requires_review"] is True

    def test_anticipo_clamped_at_zero_when_negative(self):
        # If retenciones_año_anterior >> impuesto_neto × 0.75, anticipo = 0
        settings = _make_settings()
        ledger = _make_ledger(ingresos=100_000, costos=0, gastos=0)
        # Large prior-year retenciones
        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.sum_retenciones_anio": MagicMock(
                    side_effect=[Decimal("10000"), Decimal("9999999")]
                ),
            },
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["95"]["value"] >= 0.0

    def test_saldo_final_is_saldo_plus_anticipo(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["96"]["value"] == pytest.approx(
            fields["93"]["value"] + fields["95"]["value"], rel=1e-3
        )


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


class TestF110Warnings:
    def test_perdida_fiscal_warning_when_rlo_negative(self):
        settings = _make_settings()
        ledger = _make_ledger(ingresos=100_000, costos=300_000, gastos=200_000)
        draft = _generate_f110(settings, ledger)
        warning_fields = [w["field"] for w in draft.warnings_json]
        assert "f110_renta_liquida_ordinaria" in warning_fields

    def test_saldo_a_favor_warning_when_retenciones_exceed_impuesto(self):
        settings = _make_settings()
        # Low income → low impuesto, but high retenciones
        ledger = _make_ledger(ingresos=200_000, costos=0, gastos=0, retenciones=0)
        draft = _generate_f110(
            settings,
            ledger,
            extra_patches={
                "app.services.db_service.sum_retenciones_anio": MagicMock(
                    return_value=Decimal("999999")
                ),
            },
        )
        warning_fields = [w["field"] for w in draft.warnings_json]
        assert "93" in warning_fields

    def test_general_f2516_warning_always_present(self):
        settings = _make_settings()
        ledger = _make_ledger()
        draft = _generate_f110(settings, ledger)
        warning_fields = [w["field"] for w in draft.warnings_json]
        assert "general" in warning_fields


# ---------------------------------------------------------------------------
# TarifaRenta integration — ESAL 20%, financiero 2026 55%, hidroeléctrico 38%
# ---------------------------------------------------------------------------


def _generate_f110_with_tarifa(
    settings, ledger, tarifa_info: dict | None, year: int = 2026
):
    """Helper: generate F110 draft with get_tarifa_renta patched to return tarifa_info."""
    db = _mock_db_no_f2516_no_perdidas(settings, year)
    with (
        patch(
            "app.services.tax_declaration_service.db_service.get_general_ledger",
            return_value=ledger,
        ),
        patch(
            "app.services.db_service.get_perdidas_disponibles",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.services.db_service.sum_perdidas_disponibles",
            MagicMock(return_value=Decimal("0")),
        ),
        patch(
            "app.services.db_service.sum_retenciones_anio",
            MagicMock(return_value=Decimal("0")),
        ),
        patch(
            "app.services.db_service.get_latest_f2516_reviewed",
            MagicMock(return_value=None),
        ),
        patch(
            "app.services.tax_declaration_service.db_service.get_tarifa_renta",
            return_value=tarifa_info,
        ),
    ):
        draft = generate_declaration_draft(
            db, "900123456", "F110", date(year, 1, 1), date(year, 12, 31)
        )
    return draft


class TestF110WithTarifaRenta:
    """Tests that _build_f110 applies the regulatory tarifa when available."""

    def test_esal_20_percent(self):
        """ESAL regime uses 20% tarifa from tarifas_renta table."""
        settings = _make_settings(tasa_renta=Decimal("0.20"))
        setattr(settings, "regimen_tributario", "esal")
        setattr(settings, "actividad_economica", "general")

        # ica_511505=0 to avoid class-5 sum including it
        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )
        tarifa_info = {
            "tarifa_base": 0.20,
            "sobretasa": 0.0,
            "tarifa_efectiva": 0.20,
            "base_legal": "Art. 19 ET",
        }

        draft = _generate_f110_with_tarifa(settings, ledger, tarifa_info)
        fields = {f["renglon"]: f for f in draft.fields_json}

        # RLG = 2_000_000 - 500_000 - 300_000 = 1_200_000
        # impuesto_basico = 1_200_000 × 0.20 = 240_000
        assert fields["80"]["value"] == pytest.approx(240_000.0, rel=1e-3)
        assert "Art. 19 ET" in fields["80"]["label"]

    def test_financiero_2026_sobretasa_emergencia(self):
        """Sector financiero 2026: 35% base + 20% sobretasa (Decreto 0150) = 55%."""
        settings = _make_settings(tasa_renta=Decimal("0.35"))
        setattr(settings, "regimen_tributario", "ordinario")
        setattr(settings, "actividad_economica", "financiero")

        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )
        tarifa_info = {
            "tarifa_base": 0.35,
            "sobretasa": 0.20,
            "tarifa_efectiva": 0.55,
            "base_legal": "Decreto 0150/2026 emergencia económica",
        }

        draft = _generate_f110_with_tarifa(settings, ledger, tarifa_info, year=2026)
        fields = {f["renglon"]: f for f in draft.fields_json}

        # RLG = 2M - 500k - 300k = 1_200_000, impuesto_basico = 1_200_000 × 0.55 = 660_000
        assert fields["80"]["value"] == pytest.approx(660_000.0, rel=1e-3)
        assert "Decreto 0150" in fields["80"]["label"]

    def test_hidroelectrico_38_percent(self):
        """Hidroeléctricas: 35% + 3% sobretasa = 38%."""
        settings = _make_settings(tasa_renta=Decimal("0.35"))
        setattr(settings, "regimen_tributario", "ordinario")
        setattr(settings, "actividad_economica", "hidroelectrico")

        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )
        tarifa_info = {
            "tarifa_base": 0.35,
            "sobretasa": 0.03,
            "tarifa_efectiva": 0.38,
            "base_legal": "Art. 240 par. 5 ET",
        }

        draft = _generate_f110_with_tarifa(settings, ledger, tarifa_info)
        fields = {f["renglon"]: f for f in draft.fields_json}

        # RLG = 2M - 500k - 300k = 1_200_000, impuesto_basico = 1_200_000 × 0.38 = 456_000
        assert fields["80"]["value"] == pytest.approx(456_000.0, rel=1e-3)
        assert "Art. 240 par. 5 ET" in fields["80"]["label"]

    def test_fallback_to_tasa_renta_when_no_tarifa(self):
        """When get_tarifa_renta returns None, falls back to settings.tasa_renta (0.35)."""
        settings = _make_settings(tasa_renta=Decimal("0.35"))
        setattr(settings, "regimen_tributario", "ordinario")
        setattr(settings, "actividad_economica", "general")

        ledger = _make_ledger(
            ingresos=2_000_000, costos=500_000, gastos=300_000, ica_511505=0
        )

        draft = _generate_f110_with_tarifa(settings, ledger, tarifa_info=None)
        fields = {f["renglon"]: f for f in draft.fields_json}

        # RLG = 2M - 500k - 300k = 1_200_000, fallback 35% → 420_000
        assert fields["80"]["value"] == pytest.approx(420_000.0, rel=1e-3)
