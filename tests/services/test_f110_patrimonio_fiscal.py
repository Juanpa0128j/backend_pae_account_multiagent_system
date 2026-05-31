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
    def test_activos_overridden_from_f2516_when_reviewed(self):
        f2516 = MagicMock()
        f2516.fields_json = [
            {"renglon": "199", "value": 5_000_000.0, "label": "Activos fiscales"},
            {"renglon": "249", "value": 1_000_000.0, "label": "Pasivos fiscales"},
        ]
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=f2516)
        fields = _by_renglon(draft)
        assert fields["26"]["value"] == pytest.approx(5_000_000.0)
        assert fields["26"]["source"] == "f2516:199"

    def test_pasivos_overridden_from_f2516_when_reviewed(self):
        f2516 = MagicMock()
        f2516.fields_json = [
            {"renglon": "199", "value": 5_000_000.0, "label": "Activos"},
            {"renglon": "249", "value": 1_000_000.0, "label": "Pasivos"},
        ]
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=f2516)
        fields = _by_renglon(draft)
        assert fields["27"]["value"] == pytest.approx(1_000_000.0)
        assert fields["27"]["source"] == "f2516:249"

    def test_patrimonio_liquido_fiscal_calculated_from_f2516(self):
        f2516 = MagicMock()
        f2516.fields_json = [
            {"renglon": "199", "value": 5_000_000.0, "label": "Activos"},
            {"renglon": "249", "value": 1_000_000.0, "label": "Pasivos"},
        ]
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=f2516)
        fields = _by_renglon(draft)
        assert "29" in fields
        assert fields["29"]["value"] == pytest.approx(4_000_000.0)
        assert fields["29"]["requires_review"] is False
        assert fields["29"]["source"] == "f2516:290"


class TestF110PatrimonioFallback:
    def test_falls_back_to_clase_1_2_when_no_f2516_reviewed(self):
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=None)
        fields = _by_renglon(draft)
        # Activos clase 1 = 800_000; Pasivos clase 2 = 300_000
        assert fields["26"]["value"] == pytest.approx(800_000.0)
        assert fields["27"]["value"] == pytest.approx(300_000.0)
        assert fields["26"]["source"] == "clase_1_puc"

    def test_fallback_emits_warning_on_renglon_26(self):
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=None)
        warn_fields = [w["field"] for w in draft.warnings_json]
        assert "26" in warn_fields

    def test_patrimonio_29_requires_review_when_fallback(self):
        draft = _generate_f110(_make_settings(), _make_ledger(), f2516_mock=None)
        fields = _by_renglon(draft)
        assert fields["29"]["requires_review"] is True
        assert fields["29"]["value"] == pytest.approx(500_000.0)
