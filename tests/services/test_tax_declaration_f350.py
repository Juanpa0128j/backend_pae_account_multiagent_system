"""F350 — nómina (salarios Art. 383) y mapeo de conceptos a casillas oficiales."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.tax_declaration_service import _build_f350, build_draft_from_catalog
from app.services.dian_forms import get_catalog
from app.models.database import CompanySettings


def _make_settings() -> CompanySettings:
    s = MagicMock(spec=CompanySettings)
    s.nit = "800999888"
    return s


def _draft_fields(**kw):
    """Run _build_f350 and project through the official F350 catalog."""
    computed, warnings = _build_f350(**kw)
    fields = build_draft_from_catalog(get_catalog("F350"), computed)
    return {f.renglon: f for f in fields}, warnings


class TestF350Salarios:
    """Rentas de trabajo (salarios Art. 383) → casilla 93 (retención PN)."""

    @patch("app.services.tax_declaration_service.db_service")
    def test_salarios_populated_from_nomina_casilla_93(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "salarios_383",
                "categoria": "salarios",
                "renglon_350": "93",
                "aplica_a": "PN",
                "label": "Retenciones sobre salarios — Art. 383 ET",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("0")
        mock_db_svc.sum_nomina_retefuente.return_value = 2_400_000.0
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _draft_fields(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )
        assert fields["93"].value == 2_400_000.0

    @patch("app.services.tax_declaration_service.db_service")
    def test_salarios_zero_when_no_nomina(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "salarios_383",
                "categoria": "salarios",
                "renglon_350": "93",
                "aplica_a": "PN",
                "label": "Retenciones sobre salarios — Art. 383 ET",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("0")
        mock_db_svc.sum_nomina_retefuente.return_value = 0.0
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _draft_fields(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )
        # Sin nómina la casilla 93 queda en 0 (sin_movimiento).
        assert fields["93"].value == 0.0

    @patch("app.services.tax_declaration_service.db_service")
    def test_salarios_included_in_total_130(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "salarios_383",
                "categoria": "salarios",
                "renglon_350": "93",
                "aplica_a": "PN",
                "label": "Retenciones sobre salarios — Art. 383 ET",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("0")
        mock_db_svc.sum_nomina_retefuente.return_value = 1_000_000.0
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _draft_fields(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )
        # Total retenciones renta (casilla 130) incluye la retención de salarios.
        assert fields["130"].value == 1_000_000.0


class TestF350ConceptMapping:
    """Conceptos sin mapeo directo caen en 'Otros pagos'; ReteICA se excluye."""

    @patch("app.services.tax_declaration_service.db_service")
    def test_hidrocarburos_routed_to_otros(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "hidrocarburos_pj",
                "categoria": "hidrocarburos",
                "renglon_350": "40",
                "aplica_a": "PJ",
                "label": "Compra de hidrocarburos",
                "tarifa_default": 0.01,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("500000")
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _draft_fields(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )
        # Concepto sin casilla propia → "Otros pagos sujetos a retención" (54, PJ).
        assert fields["54"].value == 500_000.0
        # Base estimada = 500.000 / 1% = 50.000.000 → casilla 41.
        assert fields["41"].value == 50_000_000.0

    @patch("app.services.tax_declaration_service.db_service")
    def test_reteica_concept_excluded(self, mock_db_svc):
        mock_db_svc.list_tax_concepts.return_value = [
            {
                "code": "reteica",
                "categoria": "ica",
                "renglon_350": "76",
                "aplica_a": "AMB",
                "label": "ReteICA",
                "tarifa_default": None,
            }
        ]
        mock_db_svc.sum_retencion_by_concepto.return_value = Decimal("9660")
        mock_db_svc.count_unclassified_retenciones.return_value = 0

        fields, _ = _draft_fields(
            ledger=[],
            settings=_make_settings(),
            db=MagicMock(),
            company_nit="800999888",
        )
        # ReteICA es municipal → no aporta a ninguna casilla del F350.
        assert fields["130"].value == 0.0
        assert not any("ICA" in f.label for f in fields.values())
