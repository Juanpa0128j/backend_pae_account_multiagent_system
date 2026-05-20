"""
Document classifier service.

Classifies uploaded documents by type and ingestion pathway. Delegates all
LLM invocation to `LLMClient`, which handles the OpenAI → Gemini → Groq
fallback chain per CLAUDE.md conventions.
"""

import logging
from typing import Literal, Optional, cast

from app.models.document_types import DocumentType, IngestPathway, get_pathway
from app.models.llm_schemas import ClassificationResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SourceFormat = Literal["pdf", "xlsx", "xml", "jpg", "jpeg", "png"]
_VALID_SOURCE_FORMATS = {"pdf", "xlsx", "xml", "jpg", "jpeg", "png"}


class DocumentClassification(BaseModel):
    """Result of classifying an uploaded document."""

    doc_type: DocumentType = Field(description="Type of document detected")
    pathway: IngestPathway = Field(description="Ingestion pathway for this document")
    confidence: float = Field(ge=0, le=1, description="Classification confidence 0-1")
    source_format: SourceFormat = Field(description="Source file format")
    period_start: Optional[str] = Field(
        default=None, description="Period start date YYYY-MM-DD if detected"
    )
    period_end: Optional[str] = Field(
        default=None, description="Period end date YYYY-MM-DD if detected"
    )
    entity_nit: Optional[str] = Field(
        default=None, description="Entity NIT if detected in document"
    )
    entity_name: Optional[str] = Field(
        default=None, description="Entity name if detected in document"
    )
    error: Optional[str] = Field(
        default=None,
        description=(
            "When set, the LLM classification failed and the result is a "
            "best-effort fallback. Callers should surface this to the user "
            "rather than silently treating the doc as 'OTRO'."
        ),
    )


def _classify_with_llm(
    text_preview: str,
    *,
    company_nit: str | None = None,
    company_name: str | None = None,
) -> ClassificationResponse:
    """Thin wrapper that calls the shared LLMClient. Isolated for test mocking."""
    from app.core.llm_client import get_llm_client

    return get_llm_client().classify_document(
        text_preview,
        company_nit=company_nit,
        company_name=company_name,
    )


def classify_document(
    text_preview: str,
    source_format: str,
    *,
    company_nit: str | None = None,
    company_name: str | None = None,
) -> DocumentClassification:
    """
    Classify a document using the shared LLMClient (OpenAI → Gemini → Groq).

    Args:
        text_preview: First ~3000 chars of extracted text.
        source_format: File extension without dot ("pdf", "xlsx", "xml", ...).
        company_nit: Receiver's NIT — lets the LLM determine factura_venta vs
            factura_compra direction by comparing against emisor/adquirente NITs
            in the document. Optional; when None the LLM uses fallback rules.
        company_name: Receiver's razón social — secondary direction signal when
            NITs are redacted or unreadable. Optional.

    Returns:
        DocumentClassification with type, pathway, and metadata.
    """
    normalized_source_format = source_format.lower().strip()
    if normalized_source_format not in _VALID_SOURCE_FORMATS:
        logger.warning(
            "doc_classifier: unsupported source_format '%s' — coercing to 'pdf'",
            source_format,
        )
        normalized_source_format = "pdf"
    source_format_literal = cast(SourceFormat, normalized_source_format)

    if not text_preview or not text_preview.strip():
        logger.warning("doc_classifier: empty text preview — defaulting to 'otro'")
        return DocumentClassification(
            doc_type=DocumentType.OTRO,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.0,
            source_format=source_format_literal,
        )

    try:
        response = _classify_with_llm(
            text_preview,
            company_nit=company_nit,
            company_name=company_name,
        )

        try:
            doc_type = DocumentType(response.doc_type)
        except ValueError:
            logger.warning(
                "doc_classifier: LLM returned unknown doc_type '%s' — using 'otro'",
                response.doc_type,
            )
            doc_type = DocumentType.OTRO

        pathway = get_pathway(doc_type)

        direction_signal = getattr(response, "direction_signal", None)
        emisor_extracted = getattr(response, "emisor_extracted", None)
        entity_nit_value = (response.entity_nit or "").strip()

        # Defensive override for factura_venta hallucinations. Two paths:
        # 1) LLM reported nit_match_emisor without extracting a NIT
        # 2) emisor_extracted is set but does NOT contain company_name
        #    (substring match either way) — proveedor externo, must be compra.
        emisor_lower = (emisor_extracted or "").strip().lower()
        company_lower = (company_name or "").strip().lower()
        emisor_mismatch = (
            bool(emisor_lower)
            and bool(company_lower)
            and company_lower not in emisor_lower
            and emisor_lower not in company_lower
        )
        if doc_type == DocumentType.FACTURA_VENTA and (
            (direction_signal == "nit_match_emisor" and not entity_nit_value)
            or emisor_mismatch
        ):
            override_reason = (
                "override_no_nit_evidence"
                if not entity_nit_value
                else "override_emisor_mismatch"
            )
            logger.warning(
                "doc_classifier: overriding factura_venta -> factura_compra "
                "(reason=%s). emisor_extracted=%s, company_name=%s, "
                "entity_nit=%s, direction_signal_llm=%s",
                override_reason,
                emisor_extracted or "—",
                company_name or "—",
                entity_nit_value or "—",
                direction_signal or "—",
            )
            doc_type = DocumentType.FACTURA_COMPRA
            pathway = get_pathway(doc_type)
            direction_signal = override_reason

        classification = DocumentClassification(
            doc_type=doc_type,
            pathway=pathway,
            confidence=response.confidence,
            source_format=source_format_literal,
            period_start=response.period_start,
            period_end=response.period_end,
            entity_nit=response.entity_nit,
            entity_name=response.entity_name,
        )

        logger.info(
            "doc_classifier: classified as %s (pathway=%s, confidence=%.2f, "
            "company_nit=%s, direction_signal=%s, emisor_extracted=%s)",
            classification.doc_type.value,
            classification.pathway.value,
            classification.confidence,
            company_nit or "—",
            direction_signal or "—",
            emisor_extracted or "—",
        )
        return classification

    except Exception as e:
        logger.error("doc_classifier: LLM classification failed: %s", e)
        # Surface the failure via the `error` field so the supervisor / caller
        # can distinguish a real "OTRO" classification from an LLM outage.
        # Caller should set state["error"] for upstream/permanent provider
        # failures rather than silently treating this as a normal classification.
        return DocumentClassification(
            doc_type=DocumentType.OTRO,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.0,
            source_format=source_format_literal,
            error=f"Classification LLM error: {type(e).__name__}: {e}",
        )
