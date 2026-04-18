"""
Document classifier service.

Classifies uploaded documents by type and ingestion pathway. Delegates all
LLM invocation to `LLMClient`, which handles the OpenAI → Gemini → Groq
fallback chain per CLAUDE.md conventions.
"""

import logging
from typing import Literal, Optional, cast

from app.models.document_types import DocumentType, IngestPathway, get_pathway
from app.models.llm_schemas import CLASSIFICATION_PROMPT, ClassificationResponse  # noqa: F401
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


def _classify_with_llm(text_preview: str) -> ClassificationResponse:
    """Thin wrapper that calls the shared LLMClient. Isolated for test mocking."""
    from app.core.llm_client import get_llm_client

    return get_llm_client().classify_document(text_preview)


def classify_document(
    text_preview: str,
    source_format: str,
) -> DocumentClassification:
    """
    Classify a document using the shared LLMClient (OpenAI → Gemini → Groq).

    Args:
        text_preview: First ~3000 chars of extracted text.
        source_format: File extension without dot ("pdf", "xlsx", "xml", ...).

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
        response = _classify_with_llm(text_preview)

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
            source_format=source_format_literal,
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
            source_format=source_format_literal,
        )
