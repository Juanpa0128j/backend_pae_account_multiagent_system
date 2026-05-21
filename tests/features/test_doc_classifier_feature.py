"""
Tests for the document classifier service.
"""

import pytest
from unittest.mock import patch

from app.models.document_types import (
    DocumentType,
    IngestPathway,
    get_pathway,
    PATHWAY_MAP,
)
from app.models.llm_schemas import ClassificationResponse
from app.services.doc_classifier import (
    DocumentClassification,
    classify_document,
)

# ---------------------------------------------------------------------------
# DocumentType and Pathway tests
# ---------------------------------------------------------------------------


class TestDocumentTypes:
    def test_all_types_have_pathway(self):
        for dt in DocumentType:
            assert dt in PATHWAY_MAP, f"DocumentType.{dt.name} missing from PATHWAY_MAP"

    def test_via_a_types(self):
        via_a = [
            DocumentType.FACTURA_VENTA,
            DocumentType.FACTURA_COMPRA,
            DocumentType.EXTRACTO_BANCARIO,
            DocumentType.NOTA_CREDITO,
            DocumentType.NOTA_DEBITO,
            DocumentType.DECLARACION_IVA,
            DocumentType.DECLARACION_RETEICA,
            DocumentType.ANEXO_TRIBUTARIO,
            DocumentType.AUXILIAR_IMPUESTO,
        ]
        for dt in via_a:
            assert get_pathway(dt) == IngestPathway.BUILD_FROM_SCRATCH

    def test_via_b_types(self):
        via_b = [
            DocumentType.BALANCE_GENERAL,
            DocumentType.ESTADO_RESULTADOS,
            DocumentType.LIBRO_AUXILIAR,
        ]
        for dt in via_b:
            assert get_pathway(dt) == IngestPathway.WORK_WITH_EXISTING

    def test_otro_defaults_to_build(self):
        assert get_pathway(DocumentType.OTRO) == IngestPathway.BUILD_FROM_SCRATCH


# ---------------------------------------------------------------------------
# DocumentClassification schema tests
# ---------------------------------------------------------------------------


class TestDocumentClassification:
    def test_valid_classification(self):
        c = DocumentClassification(
            doc_type=DocumentType.DECLARACION_IVA,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.95,
            source_format="pdf",
            entity_nit="900123456",
        )
        assert c.doc_type == DocumentType.DECLARACION_IVA
        assert c.confidence == 0.95

    def test_optional_fields_default_none(self):
        c = DocumentClassification(
            doc_type=DocumentType.FACTURA_VENTA,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.8,
            source_format="pdf",
        )
        assert c.period_start is None
        assert c.entity_nit is None

    def test_confidence_bounds(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DocumentClassification(
                doc_type=DocumentType.OTRO,
                pathway=IngestPathway.BUILD_FROM_SCRATCH,
                confidence=1.5,  # > 1
                source_format="pdf",
            )


# ---------------------------------------------------------------------------
# classify_document function tests (with mocked LLM)
# ---------------------------------------------------------------------------


class TestClassifyDocument:
    def test_empty_text_returns_otro(self):
        result = classify_document(text_preview="", source_format="pdf")
        assert result.doc_type == DocumentType.OTRO
        assert result.confidence == 0.0

    def test_whitespace_text_returns_otro(self):
        result = classify_document(text_preview="   \n  ", source_format="xlsx")
        assert result.doc_type == DocumentType.OTRO
        assert result.confidence == 0.0

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_llm_classifies_iva_declaration(self, mock_classify):
        mock_classify.return_value = ClassificationResponse(
            doc_type="declaracion_iva",
            confidence=0.92,
            period_start="2026-01-01",
            period_end="2026-02-28",
            entity_nit="900123456",
            entity_name="Distribuidora XYZ",
        )

        result = classify_document(
            text_preview="Formulario 300 IVA bimestral...",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.DECLARACION_IVA
        assert result.pathway == IngestPathway.BUILD_FROM_SCRATCH
        assert result.confidence == 0.92
        assert result.entity_nit == "900123456"

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_llm_classifies_balance_general(self, mock_classify):
        mock_classify.return_value = ClassificationResponse(
            doc_type="balance_general",
            confidence=0.88,
            entity_nit="800999888",
            entity_name=None,
        )

        result = classify_document(
            text_preview="Activos corrientes... Pasivos... Patrimonio...",
            source_format="xlsx",
        )
        assert result.doc_type == DocumentType.BALANCE_GENERAL
        assert result.pathway == IngestPathway.WORK_WITH_EXISTING

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_unknown_doc_type_falls_back_to_otro(self, mock_classify):
        mock_classify.return_value = ClassificationResponse(
            doc_type="something_unknown",
            confidence=0.5,
        )

        result = classify_document(
            text_preview="Some unknown content",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.OTRO

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_llm_failure_returns_otro(self, mock_classify):
        mock_classify.side_effect = RuntimeError("API unavailable")

        result = classify_document(
            text_preview="Any content here",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.OTRO
        assert result.confidence == 0.0

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_classifies_factura_venta(self, mock_classify):
        mock_classify.return_value = ClassificationResponse(
            doc_type="factura_venta",
            confidence=0.95,
            entity_nit="900111222",
            entity_name="Mi Empresa SAS",
        )

        result = classify_document(
            text_preview="FACTURA DE VENTA No. FV-001...",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.FACTURA_VENTA
        assert result.pathway == IngestPathway.BUILD_FROM_SCRATCH

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_classifies_auxiliar_impuesto(self, mock_classify):
        mock_classify.return_value = ClassificationResponse(
            doc_type="auxiliar_impuesto",
            confidence=0.87,
            period_end="2026-02-28",
        )

        result = classify_document(
            text_preview="Cuenta 240802 IVA Descontable... Débito Crédito Saldo",
            source_format="xlsx",
        )
        assert result.doc_type == DocumentType.AUXILIAR_IMPUESTO
        assert result.pathway == IngestPathway.BUILD_FROM_SCRATCH

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_factura_compra_when_emisor_differs_from_company(self, mock_classify):
        """Emisor (Country Club) ≠ company → factura_compra."""
        mock_classify.return_value = ClassificationResponse(
            doc_type="factura_compra",
            confidence=0.93,
            entity_nit="900390126",
            entity_name="CORPORACIÓN COUNTRY CLUB EJECUTIVOS",
            direction_signal="name_match_compra",
        )

        result = classify_document(
            text_preview="Factura Electrónica de Venta FES29664 emisor Country Club...",
            source_format="jpg",
            company_nit="901016386",
            company_name="testing_insane SAS",
        )
        assert result.doc_type == DocumentType.FACTURA_COMPRA
        assert result.direction_signal == "name_match_compra"
        mock_classify.assert_called_once()
        _, kwargs = mock_classify.call_args
        assert kwargs["company_nit"] == "901016386"
        assert kwargs["company_name"] == "testing_insane SAS"

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_factura_venta_when_emisor_matches_company(self, mock_classify):
        """Emisor NIT == company NIT → factura_venta."""
        mock_classify.return_value = ClassificationResponse(
            doc_type="factura_venta",
            confidence=0.95,
            entity_nit="901016386",
            entity_name="testing_insane SAS",
            direction_signal="nit_match_emisor",
        )

        result = classify_document(
            text_preview="Factura de Venta FV-192 emisor testing_insane SAS NIT 901016386...",
            source_format="pdf",
            company_nit="901016386",
            company_name="testing_insane SAS",
        )
        assert result.doc_type == DocumentType.FACTURA_VENTA
        assert result.direction_signal == "nit_match_emisor"

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_override_when_nit_match_emisor_has_empty_entity_nit(self, mock_classify):
        """LLM claims nit_match_emisor but did not extract a NIT → override to compra."""
        mock_classify.return_value = ClassificationResponse(
            doc_type="factura_venta",
            confidence=0.92,
            entity_nit="",
            entity_name=None,
            direction_signal="nit_match_emisor",
            emisor_extracted=None,
        )

        result = classify_document(
            text_preview="Factura Electrónica de Venta ...",
            source_format="jpg",
            company_nit="901016386",
            company_name="testing_insane SAS",
        )
        assert result.doc_type == DocumentType.FACTURA_COMPRA
        assert result.direction_signal == "override_no_nit_evidence"

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_override_when_emisor_extracted_mismatches_company(self, mock_classify):
        """Emisor extracted differs from company → override to compra even if entity_nit present."""
        mock_classify.return_value = ClassificationResponse(
            doc_type="factura_venta",
            confidence=0.94,
            entity_nit="900390126",
            entity_name="CORPORACIÓN COUNTRY CLUB EJECUTIVOS",
            direction_signal="nit_match_emisor",
            emisor_extracted="CORPORACIÓN COUNTRY CLUB EJECUTIVOS",
        )

        result = classify_document(
            text_preview="Factura Electrónica de Venta FES29664 ...",
            source_format="jpg",
            company_nit="901016386",
            company_name="testing_insane SAS",
        )
        assert result.doc_type == DocumentType.FACTURA_COMPRA
        assert result.direction_signal == "override_emisor_mismatch"
        assert result.emisor_extracted == "CORPORACIÓN COUNTRY CLUB EJECUTIVOS"

    @patch("app.services.doc_classifier._classify_with_llm")
    def test_no_override_when_emisor_matches_company_via_accent_fold(
        self, mock_classify
    ):
        """Accent-folded 'CORPORACIÓN TESTING INSANE' matches 'Corporacion Testing Insane' → no override."""
        mock_classify.return_value = ClassificationResponse(
            doc_type="factura_venta",
            confidence=0.95,
            entity_nit="901016386",
            entity_name="CORPORACIÓN TESTING INSANE SAS",
            direction_signal="nit_match_emisor",
            emisor_extracted="CORPORACIÓN TESTING INSANE SAS",
        )

        result = classify_document(
            text_preview="Factura de Venta emisor Corporacion Testing Insane ...",
            source_format="pdf",
            company_nit="901016386",
            company_name="Corporacion Testing Insane SAS",
        )
        assert result.doc_type == DocumentType.FACTURA_VENTA
        assert result.direction_signal == "nit_match_emisor"
