"""
Tests for the 45.000 UVT threshold gate on F2516 requirement (Art. 772-1 ET).

When previous-year gross income < 45.000 × UVT, F2516 is NOT obligatory for
F110 generation: generate_declaration_draft should skip the F2516 prerequisite
and emit a warning explaining why.

When previous-year gross income >= threshold, the F2516 prerequisite remains:
missing/non-reviewed F2516 raises ValueError as before.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.tax_declaration_service import generate_declaration_draft


def _make_settings():
    s = MagicMock()
    s.nit = "900123456"
    s.ciudad = "Medellín"
    s.codigo_ciiu = "6201"
    s.iva_responsable = True
    s.es_declarante = True
    s.tasa_renta = Decimal("0.35")
    s.tasa_ica = Decimal("0.00966")
    s.tasa_iva_general = Decimal("0.19")
    s.regimen_tributario = "ordinario"
    s.actividad_economica = "general"
    return s


def _small_ledger():
    """Tiny ledger → prev-year gross well below 45.000 UVT × 52.374."""
    return [
        {
            "account": "4135",
            "name": "Ingresos servicios",
            "total_debit": 0.0,
            "total_credit": 10_000.0,
            "net_balance": -10_000.0,
        },
    ]


def _big_ledger():
    """Huge ledger → prev-year gross above 45.000 UVT × 52.374 (~2.36B)."""
    return [
        {
            "account": "4135",
            "name": "Ingresos servicios",
            "total_debit": 0.0,
            "total_credit": 5_000_000_000.0,
            "net_balance": -5_000_000_000.0,
        },
    ]


def _mock_db(settings, f2516=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = settings
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = f2516
    return db


_BUILDER_PATCHES = [
    patch("app.services.db_service.get_latest_f2516_reviewed", return_value=None),
    patch(
        "app.services.db_service.sum_perdidas_disponibles", return_value=Decimal("0")
    ),
    patch("app.services.db_service.sum_retenciones_anio", return_value=Decimal("0")),
    patch("app.services.db_service.get_tarifa_renta", return_value=None),
]


def _apply_builder_patches(fn):
    for p in reversed(_BUILDER_PATCHES):
        fn = p(fn)
    return fn


class TestF110UvtThreshold:
    @_apply_builder_patches
    @patch("app.services.tax_declaration_service.db_service.get_uvt")
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_below_threshold_skips_f2516_requirement(self, mock_ledger, mock_uvt, *_p):
        """Prev-year gross < 45.000 UVT → no ValueError even with no F2516."""
        mock_uvt.return_value = Decimal("52374")
        mock_ledger.return_value = _small_ledger()
        db = _mock_db(_make_settings(), f2516=None)

        # Should NOT raise
        draft = generate_declaration_draft(
            db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
        )

        warning_msgs = [w["message"] for w in draft.warnings_json]
        assert any(
            "F2516 no obligatorio" in m and "45.000 UVT" in m for m in warning_msgs
        )

    @_apply_builder_patches
    @patch("app.services.tax_declaration_service.db_service.get_uvt")
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_above_threshold_requires_f2516(self, mock_ledger, mock_uvt, *_p):
        """Prev-year gross >= 45.000 UVT and no F2516 → ValueError."""
        mock_uvt.return_value = Decimal("52374")
        mock_ledger.return_value = _big_ledger()
        db = _mock_db(_make_settings(), f2516=None)

        with pytest.raises(ValueError, match="F2516"):
            generate_declaration_draft(
                db,
                "900123456",
                "F110",
                date(2026, 1, 1),
                date(2026, 12, 31),
            )

    @_apply_builder_patches
    @patch("app.services.tax_declaration_service.db_service.get_uvt")
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_above_threshold_with_reviewed_f2516_succeeds(
        self, mock_ledger, mock_uvt, *_p
    ):
        """Prev-year gross >= 45.000 UVT, reviewed F2516 present → succeeds."""
        mock_uvt.return_value = Decimal("52374")
        mock_ledger.return_value = _big_ledger()
        f2516 = MagicMock()
        f2516.status = "reviewed"
        f2516.fields_json = []
        db = _mock_db(_make_settings(), f2516=f2516)

        draft = generate_declaration_draft(
            db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
        )

        warning_msgs = [w["message"] for w in draft.warnings_json]
        # The "no obligatorio" warning must NOT be present
        assert not any("F2516 no obligatorio" in m for m in warning_msgs)

    @_apply_builder_patches
    @patch("app.services.tax_declaration_service.db_service.get_uvt")
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_below_threshold_emits_explicit_warning_field(
        self, mock_ledger, mock_uvt, *_p
    ):
        """When skipped, warning entry must reference the f2516 field."""
        mock_uvt.return_value = Decimal("52374")
        mock_ledger.return_value = _small_ledger()
        db = _mock_db(_make_settings(), f2516=None)

        draft = generate_declaration_draft(
            db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
        )

        f2516_warnings = [w for w in draft.warnings_json if w["field"] == "f2516"]
        assert len(f2516_warnings) == 1
