"""
Document classifier service.

Classifies uploaded documents by type and ingestion pathway using LLM
analysis of document content. Always uses Gemini for classification —
no filename-based heuristics.
"""

import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.document_types import DocumentType, IngestPathway, get_pathway

logger = logging.getLogger(__name__)


class DocumentClassification(BaseModel):
    """Result of classifying an uploaded document."""

    doc_type: DocumentType = Field(description="Type of document detected")
    pathway: IngestPathway = Field(description="Ingestion pathway for this document")
    confidence: float = Field(ge=0, le=1, description="Classification confidence 0-1")
    source_format: Literal["pdf", "xlsx", "xml"] = Field(
        description="Source file format"
    )
    period_start: Optional[str] = Field(
        None, description="Period start date YYYY-MM-DD if detected"
    )
    period_end: Optional[str] = Field(
        None, description="Period end date YYYY-MM-DD if detected"
    )
    entity_nit: Optional[str] = Field(
        None, description="Entity NIT if detected in document"
    )
    entity_name: Optional[str] = Field(
        None, description="Entity name if detected in document"
    )


# The classification prompt with full taxonomy
CLASSIFICATION_PROMPT = """Eres un experto contable colombiano. Analiza el siguiente contenido extraído de un documento y clasifícalo.

Tipos de documento posibles:

DOCUMENTOS FUENTE (para construir contabilidad desde cero):
- factura_venta: Factura de venta emitida. Contiene datos del vendedor, comprador, items/servicios vendidos, subtotal, IVA, total.
- factura_compra: Factura de compra recibida. Similar a factura de venta pero desde la perspectiva del comprador.
- extracto_bancario: Extracto o estado de cuenta bancario. Lista de movimientos con fechas, conceptos, débitos, créditos y saldos.
- nota_credito: Nota crédito comercial. Reduce el valor de una factura previamente emitida.
- nota_debito: Nota débito comercial. Incrementa el valor de una factura previamente emitida.
- declaracion_iva: Declaración bimestral de IVA ante la DIAN. Formulario con renglones numerados, IVA generado vs descontable, saldo a pagar/favor.
- declaracion_reteica: Declaración de retención de ICA o autorretención. Formulario municipal con bases gravables y tarifas por actividad.
- anexo_tributario: Anexo o soporte de una declaración tributaria. Tabla detallada con terceros (NIT, razón social), bases, tarifas, retenciones.
- auxiliar_impuesto: Libro auxiliar de un impuesto específico (ej: auxiliar de IVA). Movimientos contables de cuentas de impuestos con débitos, créditos, saldos.

ESTADOS FINANCIEROS EXISTENTES (para usar directamente en reportes):
- balance_general: Balance general / Estado de situación financiera. Muestra activos, pasivos y patrimonio con saldos por cuenta PUC.
- estado_resultados: Estado de resultados / PyG. Muestra ingresos, costos, gastos y utilidad neta con cuentas PUC clase 4, 5, 6.
- libro_auxiliar: Libro auxiliar contable general. Registro detallado de movimientos por cuenta con fechas, terceros, débitos, créditos, saldo corrido.

- otro: Documento que no encaja en ninguna de las categorías anteriores.

Contenido del documento:
---
{text_preview}
---

Clasifica el documento. Si el documento contiene cuentas PUC con saldos organizados jerárquicamente, probablemente es un estado financiero existente.
Si contiene transacciones individuales con fecha, NIT, valores, es un documento fuente.
Extrae también el NIT de la entidad, el nombre, y el período si están presentes."""


class _ClassificationResponse(BaseModel):
    """Schema for Gemini structured output during classification."""

    doc_type: str = Field(description="One of the document type values listed")
    confidence: float = Field(
        ge=0, le=1, description="How confident you are in this classification"
    )
    period_start: Optional[str] = Field(
        None, description="Start of the period covered, YYYY-MM-DD"
    )
    period_end: Optional[str] = Field(
        None, description="End of the period covered, YYYY-MM-DD"
    )
    entity_nit: Optional[str] = Field(None, description="NIT of the entity")
    entity_name: Optional[str] = Field(None, description="Name of the entity")


def classify_document(
    text_preview: str,
    source_format: str,
) -> DocumentClassification:
    """
    Classify a document using Gemini LLM based on its content.

    Args:
        text_preview: First ~3000 chars of extracted text.
        source_format: File extension without dot ("pdf", "xlsx", "xml").

    Returns:
        DocumentClassification with type, pathway, and metadata.
    """
    if not text_preview or not text_preview.strip():
        logger.warning("doc_classifier: empty text preview — defaulting to 'otro'")
        return DocumentClassification(
            doc_type=DocumentType.OTRO,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.0,
            source_format=source_format,
        )

    try:
        from app.core.gemini_client import get_gemini_client

        client = get_gemini_client()
        response = client.classify_document(text_preview)

        # Parse the doc_type string into the enum
        try:
            doc_type = DocumentType(response.doc_type)
        except ValueError:
            logger.warning(
                "doc_classifier: LLM returned unknown doc_type '%s' — using 'otro'",
                response.doc_type,
            )
            doc_type = DocumentType.OTRO

        pathway = get_pathway(doc_type)

        classification = DocumentClassification(
            doc_type=doc_type,
            pathway=pathway,
            confidence=response.confidence,
            source_format=source_format,
            period_start=response.period_start,
            period_end=response.period_end,
            entity_nit=response.entity_nit,
            entity_name=response.entity_name,
        )

        logger.info(
            "doc_classifier: classified as %s (pathway=%s, confidence=%.2f)",
            classification.doc_type.value,
            classification.pathway.value,
            classification.confidence,
        )
        return classification

    except Exception as e:
        logger.error("doc_classifier: LLM classification failed: %s", e)
        return DocumentClassification(
            doc_type=DocumentType.OTRO,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.0,
            source_format=source_format,
        )
