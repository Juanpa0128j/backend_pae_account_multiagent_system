"""Tests for F350 renglón 50 auto-population from nómina."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.tax_declaration_service import _build_f350
from app.models.database import CompanySettings


def _make_settings() -> CompanySettings:
    s = MagicMock(spec=CompanySettings)
    s.nit = "800999888"
    return s


class TestF350Renglon50:
    """Renglón 50 auto-populates from nómina retefuente."""

    @patch("app.services.tax_declaration_service.db_service")
    def test_renglon_50_populated_from_nomina(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "salarios_383",
                "categoria": "salarios",
                "renglon_350": "50",
                "aplica_a": "PN",
                "label": "Retenciones sobre salarios — Art. 383 ET",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("0")
        mock_db_svc.sum_nomina_retefuente.return_value = 2_400_000.0
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, warnings = _build_f350(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )

        renglon_50 = next((f for f in fields if f.renglon == "50"), None)
        assert renglon_50 is not None
        assert renglon_50.value == 2_400_000.0
        assert renglon_50.requires_review is False

    @patch("app.services.tax_declaration_service.db_service")
    def test_renglon_50_zero_when_no_nomina(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "salarios_383",
                "categoria": "salarios",
                "renglon_350": "50",
                "aplica_a": "PN",
                "label": "Retenciones sobre salarios — Art. 383 ET",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("0")
        mock_db_svc.sum_nomina_retefuente.return_value = 0.0
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _build_f350(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )

        # Renglón 50 should not be emitted when monto is 0
        renglon_50 = next((f for f in fields if f.renglon == "50"), None)
        assert renglon_50 is None

    @patch("app.services.tax_declaration_service.db_service")
    def test_renglon_50_included_in_total(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "salarios_383",
                "categoria": "salarios",
                "renglon_350": "50",
                "aplica_a": "PN",
                "label": "Retenciones sobre salarios — Art. 383 ET",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("0")
        mock_db_svc.sum_nomina_retefuente.return_value = 1_000_000.0
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _build_f350(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )

        total = next(f for f in fields if f.renglon == "_total_retenciones")
        assert total.value == 1_000_000.0
