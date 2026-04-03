"""
Tests for the document classifier service.
"""

import pytest
from unittest.mock import MagicMock, patch
from decimal import Decimal

from app.models.document_types import DocumentType, IngestPathway, get_pathway, PATHWAY_MAP
from app.services.doc_classifier import (
    DocumentClassification,
    classify_document,
    _ClassificationResponse,
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

    @patch("app.services.doc_classifier._get_classifier_chain")
    def test_llm_classifies_iva_declaration(self, mock_chain_fn):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _ClassificationResponse(
            doc_type="declaracion_iva",
            confidence=0.92,
            period_start="2026-01-01",
            period_end="2026-02-28",
            entity_nit="900123456",
            entity_name="Distribuidora XYZ",
        )
        mock_chain_fn.return_value = (mock_chain, "mock/test")

        result = classify_document(
            text_preview="Formulario 300 IVA bimestral...",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.DECLARACION_IVA
        assert result.pathway == IngestPathway.BUILD_FROM_SCRATCH
        assert result.confidence == 0.92
        assert result.entity_nit == "900123456"

    @patch("app.services.doc_classifier._get_classifier_chain")
    def test_llm_classifies_balance_general(self, mock_chain_fn):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _ClassificationResponse(
            doc_type="balance_general",
            confidence=0.88,
            entity_nit="800999888",
            entity_name=None,
        )
        mock_chain_fn.return_value = (mock_chain, "mock/test")

        result = classify_document(
            text_preview="Activos corrientes... Pasivos... Patrimonio...",
            source_format="xlsx",
        )
        assert result.doc_type == DocumentType.BALANCE_GENERAL
        assert result.pathway == IngestPathway.WORK_WITH_EXISTING

    @patch("app.services.doc_classifier._get_classifier_chain")
    def test_unknown_doc_type_falls_back_to_otro(self, mock_chain_fn):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _ClassificationResponse(
            doc_type="something_unknown",
            confidence=0.5,
        )
        mock_chain_fn.return_value = (mock_chain, "mock/test")

        result = classify_document(
            text_preview="Some unknown content",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.OTRO

    @patch("app.services.doc_classifier._get_classifier_chain")
    def test_llm_failure_returns_otro(self, mock_chain_fn):
        mock_chain_fn.side_effect = RuntimeError("API unavailable")

        result = classify_document(
            text_preview="Any content here",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.OTRO
        assert result.confidence == 0.0

    @patch("app.services.doc_classifier._get_classifier_chain")
    def test_classifies_factura_venta(self, mock_chain_fn):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _ClassificationResponse(
            doc_type="factura_venta",
            confidence=0.95,
            entity_nit="900111222",
            entity_name="Mi Empresa SAS",
        )
        mock_chain_fn.return_value = (mock_chain, "mock/test")

        result = classify_document(
            text_preview="FACTURA DE VENTA No. FV-001...",
            source_format="pdf",
        )
        assert result.doc_type == DocumentType.FACTURA_VENTA
        assert result.pathway == IngestPathway.BUILD_FROM_SCRATCH

    @patch("app.services.doc_classifier._get_classifier_chain")
    def test_classifies_auxiliar_impuesto(self, mock_chain_fn):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = _ClassificationResponse(
            doc_type="auxiliar_impuesto",
            confidence=0.87,
            period_end="2026-02-28",
        )
        mock_chain_fn.return_value = (mock_chain, "mock/test")

        result = classify_document(
            text_preview="Cuenta 240802 IVA Descontable... Débito Crédito Saldo",
            source_format="xlsx",
        )
        assert result.doc_type == DocumentType.AUXILIAR_IMPUESTO
        assert result.pathway == IngestPathway.BUILD_FROM_SCRATCH
