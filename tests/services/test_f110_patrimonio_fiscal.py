"""F110 patrimonio fiscal derivation from F2516 (renglones 26, 27, 29)."""

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
    s.tasa_ica = Decimal("0.00690")
    return s


def _make_ledger():
    return [
        {
            "account": "1105",
            "name": "Caja",
            "total_debit": 800_000.0,
            "total_credit": 0.0,
            "net_balance": 800_000.0,
        },
        {
            "account": "2105",
            "name": "Bancos",
            "total_debit": 0.0,
            "total_credit": 300_000.0,
            "net_balance": -300_000.0,
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
            "name": "Costo",
            "total_debit": 500_000.0,
            "total_credit": 0.0,
            "net_balance": 500_000.0,
        },
        {
            "account": "5110",
            "name": "Gastos",
            "total_debit": 300_000.0,
            "total_credit": 0.0,
            "net_balance": 300_000.0,
        },
    ]


def _generate_f110(settings, ledger, f2516_mock=None, year: int = 2026):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = settings
    # gate F2516 prereq must return a "reviewed" draft so F110 generation proceeds
    gate = MagicMock()
    gate.status = "reviewed"
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = gate

    with (
        patch(
            "app.services.tax_declaration_service.db_service.get_general_ledger",
            return_value=ledger,
        ),
        patch(
            "app.services.tax_declaration_service.db_service.get_uvt",
            return_value=Decimal("52374"),
        ),
        patch("app.services.db_service.get_perdidas_disponibles", return_value=[]),
        patch(
            "app.services.db_service.sum_perdidas_disponibles",
            return_value=Decimal("0"),
        ),
        patch(
            "app.services.db_service.sum_retenciones_anio",
            return_value=Decimal("0"),
        ),
        patch(
            "app.services.db_service.get_latest_f2516_reviewed",
            return_value=f2516_mock,
        ),
    ):
        return generate_declaration_draft(
            db, "900123456", "F110", date(year, 1, 1), date(year, 12, 31)
        )


def _by_renglon(draft):
    return {f["renglon"]: f for f in draft.fields_json}


class TestF110PatrimonioFromF2516:
    def test_pasivo_fiscal_from_f2516_casilla_45(self):
        f2516 = MagicMock()
        f2516.fields_json = [
            {"renglon": "199", "value": 5_000_000.0, "label": "Activos fiscales"},
            {"renglon": "249", "value": 1_000_000.0, "label": "Pasivos fiscales"},
        ]
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=f2516)
        fields = _by_renglon(draft)
        # Pasivo fiscal (F2516:249) → casilla 45
        assert fields["45"]["value"] == pytest.approx(1_000_000.0)
        assert fields["45"]["source"] == "f2516:249"

    def test_activos_fiscal_difference_warns_on_casilla_44(self):
        f2516 = MagicMock()
        f2516.fields_json = [
            {"renglon": "199", "value": 5_000_000.0, "label": "Activos"},
            {"renglon": "249", "value": 1_000_000.0, "label": "Pasivos"},
        ]
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=f2516)
        fields = _by_renglon(draft)
        # El desglose 36-43 es contable (activos clase 1 = 800k) → casilla 44
        assert fields["44"]["value"] == pytest.approx(800_000.0)
        # y se advierte que difiere del activo fiscal del F2516.
        warn = " ".join(w["message"] for w in draft.warnings_json if w["field"] == "44")
        assert "F2516" in warn

    def test_patrimonio_liquido_casilla_46(self):
        f2516 = MagicMock()
        f2516.fields_json = [
            {"renglon": "199", "value": 5_000_000.0, "label": "Activos"},
            {"renglon": "249", "value": 1_000_000.0, "label": "Pasivos"},
        ]
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=f2516)
        fields = _by_renglon(draft)
        # Patrimonio líquido (casilla 46) = 44 - 45 = 800k - 1M = -200k
        assert fields["46"]["value"] == pytest.approx(-200_000.0)


class TestF110PatrimonioFallback:
    def test_falls_back_to_clase_1_2_when_no_f2516_reviewed(self):
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=None)
        fields = _by_renglon(draft)
        # Activos clase 1 = 800_000 (casilla 44); Pasivos clase 2 = 300_000 (casilla 45)
        assert fields["44"]["value"] == pytest.approx(800_000.0)
        assert fields["45"]["value"] == pytest.approx(300_000.0)
        assert fields["45"]["source"] == "clase_2_puc"

    def test_fallback_emits_warning_on_casilla_44(self):
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=None)
        warn_fields = [w["field"] for w in draft.warnings_json]
        assert "44" in warn_fields

    def test_patrimonio_liquido_46_fallback(self):
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=None)
        fields = _by_renglon(draft)
        # 800k activos - 300k pasivos = 500k
        assert fields["46"]["value"] == pytest.approx(500_000.0)
