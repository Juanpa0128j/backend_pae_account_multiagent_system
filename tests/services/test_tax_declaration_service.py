"""
Unit tests for tax_declaration_service.

DB session and CompanySettings are mocked so no real DB is needed.
Each form builder (_build_f300, _build_f350, _build_f110, _build_ica) is
tested in isolation via generate_declaration_draft with a mock session.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.tax_declaration_service import (
    generate_declaration_draft,
    update_draft_field,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    s = MagicMock()
    s.nit = "900123456"
    s.ciudad = "Medellín"
    s.codigo_ciiu = "6201"
    s.iva_responsable = True
    s.es_declarante = True
    s.tasa_renta = Decimal("0.35")
    s.tasa_ica = Decimal("0.00966")
    s.tasa_iva_general = Decimal("0.19")
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_ledger():
    return [
        {
            "account": "240805",
            "name": "IVA Generado",
            "total_debit": 0.0,
            "total_credit": 1_900_000.0,
            "net_balance": -1_900_000.0,
        },
        {
            "account": "240802",
            "name": "IVA Descontable",
            "total_debit": 380_000.0,
            "total_credit": 0.0,
            "net_balance": 380_000.0,
        },
        {
            "account": "2365",
            "name": "Retefuente por pagar",
            "total_debit": 0.0,
            "total_credit": 60_000.0,
            "net_balance": -60_000.0,
        },
        {
            "account": "2368",
            "name": "ReteICA por pagar",
            "total_debit": 5_800.0,
            "total_credit": 9_660.0,
            "net_balance": -3_860.0,
        },
        {
            "account": "4135",
            "name": "Ingresos servicios",
            "total_debit": 0.0,
            "total_credit": 1_500_000.0,
            "net_balance": -1_500_000.0,
        },
        {
            "account": "5110",
            "name": "Honorarios",
            "total_debit": 500_000.0,
            "total_credit": 0.0,
            "net_balance": 500_000.0,
        },
        {
            "account": "6135",
            "name": "Costo servicios",
            "total_debit": 200_000.0,
            "total_credit": 0.0,
            "net_balance": 200_000.0,
        },
        {
            "account": "511505",
            "name": "ICA administración",
            "total_debit": 14_490.0,
            "total_credit": 0.0,
            "net_balance": 14_490.0,
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
            "total_debit": 40_000.0,
            "total_credit": 0.0,
            "net_balance": 40_000.0,
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


def _mock_db(settings):
    db = MagicMock()
    # Default first() chain (e.g. CompanySettings lookup) returns settings.
    db.query.return_value.filter.return_value.first.return_value = settings
    # F110 generation also calls .order_by().first() for the F2516 prerequisite
    # check; provide a reviewed F2516 by default so generic tests don't fail.
    f2516 = MagicMock()
    f2516.status = "reviewed"
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = f2516
    return db


# ---------------------------------------------------------------------------
# F300 tests
# ---------------------------------------------------------------------------


class TestF300Draft:
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_iva_generado_field_populated(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["42"]["value"] == pytest.approx(1_900_000.0)
        assert fields["42"]["requires_review"] is False

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_prorrateo_operaciones_mixtas(self, mock_ledger):
        """Test mixed operations (excluded/exempt sales) trigger prorrateo."""
        settings = _make_settings()
        # Ledger: IVA generado 1M (19% rate) → base_gravada = 1M/0.19 ≈ 5.26M
        # Total ingresos 10M → ingresos_no_gravados ≈ 4.74M → operaciones_mixtas=True
        ledger = [
            {
                "account": "240805",
                "name": "IVA Generado",
                "total_debit": 0.0,
                "total_credit": 1_000_000.0,
                "net_balance": -1_000_000.0,
            },
            {
                "account": "240802",
                "name": "IVA Descontable",
                "total_debit": 500_000.0,
                "total_credit": 0.0,
                "net_balance": 500_000.0,
            },
            {
                "account": "4135",
                "name": "Ingresos",
                "total_debit": 0.0,
                "total_credit": 10_000_000.0,
                "net_balance": -10_000_000.0,
            },
        ]
        mock_ledger.return_value = ledger
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        # Field 66 should be marked requires_review=True when prorated
        assert fields["66"]["requires_review"] is True
        # Field 66_base (total before prorrateo) should exist
        assert "66_base" in fields
        assert fields["66_base"]["value"] == pytest.approx(500_000.0)
        assert fields["66_base"]["requires_review"] is True
        # Prorated value should be less than original
        expected_factor = (1_000_000.0 / 0.19) / 10_000_000.0
        expected_prorated = round(500_000.0 * expected_factor, 2)
        assert fields["66"]["value"] == pytest.approx(expected_prorated)
        # Warning should be emitted for field 66
        warning_fields = {w["field"] for w in draft.warnings_json}
        assert "66" in warning_fields

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_non_iva_responsable_skips_prorrateo(self, mock_ledger):
        """Non-IVA companies should not trigger prorrateo even if ingresos > implied base."""
        settings = _make_settings(iva_responsable=False)
        ledger = [
            {
                "account": "240805",
                "name": "IVA Generado",
                "total_debit": 0.0,
                "total_credit": 1_000_000.0,
                "net_balance": -1_000_000.0,
            },
            {
                "account": "240802",
                "name": "IVA Descontable",
                "total_debit": 500_000.0,
                "total_credit": 0.0,
                "net_balance": 500_000.0,
            },
            {
                "account": "4135",
                "name": "Ingresos",
                "total_debit": 0.0,
                "total_credit": 10_000_000.0,
                "net_balance": -10_000_000.0,
            },
        ]
        mock_ledger.return_value = ledger
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        # Field 66 should NOT be marked requires_review when company is not iva_responsable
        assert fields["66"]["requires_review"] is False
        # Field 66_base should not exist
        assert "66_base" not in fields
        # IVA descontable should not be prorated
        assert fields["66"]["value"] == pytest.approx(500_000.0)

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_saldo_anterior_requires_review(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["84"]["requires_review"] is True
        assert fields["84"]["value"] == pytest.approx(0.0)

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_iva_neto_calculated(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["89"]["value"] == pytest.approx(
            1_520_000.0
        )  # 1_900_000 - 380_000

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_non_iva_responsable_adds_warning(self, mock_ledger):
        settings = _make_settings(iva_responsable=False)
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )

        warning_fields = {w["field"] for w in draft.warnings_json}
        assert "general" in warning_fields


# ---------------------------------------------------------------------------
# F350 tests
# ---------------------------------------------------------------------------


class TestF350Draft:
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_retefuente_from_cuenta_2365(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F350", date(2026, 1, 1), date(2026, 1, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["25"]["value"] == pytest.approx(60_000.0)
        assert fields["25"]["source"] == "cuenta_2365"

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_salarios_requires_review(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F350", date(2026, 1, 1), date(2026, 1, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["50"]["requires_review"] is True

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_reteica_from_cuenta_2368(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F350", date(2026, 1, 1), date(2026, 1, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["35"]["value"] == pytest.approx(9_660.0)
        assert fields["35"]["source"] == "cuenta_2368"


# ---------------------------------------------------------------------------
# F110 tests
# ---------------------------------------------------------------------------


_F110_DB_PATCHES = [
    patch("app.services.db_service.get_latest_f2516_reviewed", return_value=None),
    patch(
        "app.services.db_service.sum_perdidas_disponibles", return_value=Decimal("0")
    ),
    patch(
        "app.services.db_service.sum_retenciones_anio", return_value=Decimal("40000")
    ),
]


def _apply_f110_patches(fn):
    """Decorator that applies db_service patches needed by refactored _build_f110."""
    for p in reversed(_F110_DB_PATCHES):
        fn = p(fn)
    return fn


class TestF110Draft:
    @_apply_f110_patches
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_activos_from_clase_1(self, mock_ledger, *_patches):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings),
            "900123456",
            "F110",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["26"]["value"] == pytest.approx(
            840_000.0
        )  # 1105(800k) + 135518(40k)

    @_apply_f110_patches
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_ica_deducible_from_511505_521505(self, mock_ledger, *_patches):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings),
            "900123456",
            "F110",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["63"]["value"] == pytest.approx(14_490.0)

    @_apply_f110_patches
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_retenciones_favor_from_135518(self, mock_ledger, *_patches):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings),
            "900123456",
            "F110",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        # renglon "92" = retenciones from DB (patched to 40_000)
        assert fields["92"]["value"] == pytest.approx(40_000.0)

    @_apply_f110_patches
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_anticipo_requires_review(self, mock_ledger, *_patches):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings),
            "900123456",
            "F110",
            date(2026, 1, 1),
            date(2026, 12, 31),
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["95"]["requires_review"] is True


# ---------------------------------------------------------------------------
# ICA tests
# ---------------------------------------------------------------------------


class TestICADraft:
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_ingresos_from_clase_4(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "ICA", date(2026, 1, 1), date(2026, 3, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["1"]["value"] == pytest.approx(1_500_000.0)

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_ica_calculated_with_settings_rate(self, mock_ledger):
        settings = _make_settings()  # tasa_ica = 0.00966
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "ICA", date(2026, 1, 1), date(2026, 3, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        expected = round(1_500_000.0 * 0.00966, 2)
        assert fields["2"]["value"] == pytest.approx(expected)

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_avisos_tableros_15_percent(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "ICA", date(2026, 1, 1), date(2026, 3, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        ica = fields["2"]["value"]
        assert fields["3"]["value"] == pytest.approx(ica * 0.15, rel=1e-3)

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_bomberil_requires_review(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "ICA", date(2026, 1, 1), date(2026, 3, 31)
        )

        fields = {f["renglon"]: f for f in draft.fields_json}
        assert fields["4"]["requires_review"] is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestGenerateDraftErrors:
    def test_unsupported_form_type_raises(self):
        db = MagicMock()
        with pytest.raises(ValueError, match="Unsupported form_type"):
            generate_declaration_draft(
                db, "900123456", "F999", date(2026, 1, 1), date(2026, 1, 31)
            )

    def test_missing_company_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(ValueError, match="CompanySettings not found"):
            generate_declaration_draft(
                db, "000000000", "F300", date(2026, 1, 1), date(2026, 1, 31)
            )


# ---------------------------------------------------------------------------
# Disclaimer field
# ---------------------------------------------------------------------------


class TestDisclaimer:
    @_apply_f110_patches
    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_disclaimer_always_present(self, mock_ledger, *_patches):
        settings = _make_settings()
        mock_ledger.return_value = _make_ledger()

        for form in ["F300", "F350", "F110", "ICA"]:
            draft = generate_declaration_draft(
                _mock_db(settings),
                "900123456",
                form,
                date(2026, 1, 1),
                date(2026, 1, 31),
            )
            renglones = {f["renglon"] for f in draft.fields_json}
            assert "_disclaimer" in renglones, f"Missing disclaimer in {form}"


# ---------------------------------------------------------------------------
# update_draft_field
# ---------------------------------------------------------------------------


class TestUpdateDraftField:
    def test_updates_value_and_clears_review_flag(self):
        draft = MagicMock()
        draft.fields_json = [
            {
                "renglon": "84",
                "label": "Saldo anterior",
                "value": 0.0,
                "source": "x",
                "confidence": "low",
                "requires_review": True,
            },
            {
                "renglon": "89",
                "label": "Total",
                "value": 1_000.0,
                "source": "calculado",
                "confidence": "high",
                "requires_review": False,
            },
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = draft

        result = update_draft_field(db, "some-id", "84", 500_000.0)

        updated = {f["renglon"]: f for f in result.fields_json}
        assert updated["84"]["value"] == 500_000.0
        assert updated["84"]["requires_review"] is False
        assert updated["84"]["confidence"] == "high"
        assert updated["89"]["value"] == 1_000.0  # other fields unchanged

    def test_returns_none_when_draft_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = update_draft_field(db, "nonexistent", "84", 100.0)
        assert result is None

    def test_raises_on_missing_renglon(self):
        from app.services.tax_declaration_service import FieldNotFoundError

        draft = MagicMock()
        draft.fields_json = [
            {
                "renglon": "84",
                "label": "x",
                "value": 0.0,
                "source": "x",
                "confidence": "low",
                "requires_review": True,
            }
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = draft

        with pytest.raises(FieldNotFoundError):
            update_draft_field(db, "some-id", "999", 100.0)

    def test_raises_on_non_editable_field(self):
        from app.services.tax_declaration_service import FieldNotEditableError

        draft = MagicMock()
        draft.fields_json = [
            {
                "renglon": "89",
                "label": "Calculated",
                "value": 1_000.0,
                "source": "calculado",
                "confidence": "high",
                "requires_review": False,
            }
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = draft

        with pytest.raises(FieldNotEditableError):
            update_draft_field(db, "some-id", "89", 500.0)

    def test_raises_on_reserved_disclaimer(self):
        from app.services.tax_declaration_service import FieldNotEditableError

        db = MagicMock()
        with pytest.raises(FieldNotEditableError):
            update_draft_field(db, "some-id", "_disclaimer", 0.0)


# ---------------------------------------------------------------------------
# Saldo a favor preservation (Copilot review fix)
# ---------------------------------------------------------------------------


class TestSaldoAFavor:
    def _make_saldo_ledger_f300(self):
        """Ledger where IVA descontable > IVA generado → saldo a favor."""
        return [
            {
                "account": "240805",
                "name": "IVA Generado",
                "total_debit": 0.0,
                "total_credit": 500_000.0,
                "net_balance": -500_000.0,
            },
            {
                "account": "240802",
                "name": "IVA Descontable",
                "total_debit": 2_000_000.0,
                "total_credit": 0.0,
                "net_balance": 2_000_000.0,
            },
        ]

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_f300_preserves_negative_saldo_a_favor(self, mock_ledger):
        settings = _make_settings()
        mock_ledger.return_value = self._make_saldo_ledger_f300()
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        # 500k - 2000k = -1.5M saldo a favor, must NOT be clamped to 0
        assert fields["89"]["value"] == pytest.approx(-1_500_000.0)
        assert "Saldo a favor" in fields["89"]["label"]
        # Warning emitted
        warning_fields = {w["field"] for w in draft.warnings_json}
        assert "89" in warning_fields

    @patch("app.services.tax_declaration_service.db_service.get_general_ledger")
    def test_ica_preserves_negative_saldo_a_favor(self, mock_ledger):
        settings = _make_settings()
        ledger = [
            {
                "account": "4135",
                "name": "Ingresos",
                "total_debit": 0.0,
                "total_credit": 100_000.0,
                "net_balance": -100_000.0,
            },
            {
                "account": "2368",
                "name": "ReteICA recibida",
                "total_debit": 500_000.0,  # excess ReteICA
                "total_credit": 0.0,
                "net_balance": 500_000.0,
            },
        ]
        mock_ledger.return_value = ledger
        draft = generate_declaration_draft(
            _mock_db(settings), "900123456", "ICA", date(2026, 1, 1), date(2026, 3, 31)
        )
        fields = {f["renglon"]: f for f in draft.fields_json}
        # total_a_pagar = ICA + avisos - reteica_favor → negative
        assert fields["10"]["value"] < 0
        assert "Saldo a favor" in fields["10"]["label"]
