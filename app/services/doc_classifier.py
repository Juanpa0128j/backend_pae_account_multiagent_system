"""
Document classifier service.

Classifies uploaded documents by type and ingestion pathway. Delegates all
LLM invocation to `LLMClient`, which handles the OpenAI → Gemini → Groq
fallback chain per CLAUDE.md conventions.
"""

import logging
import unicodedata
from typing import Literal, Optional, cast

from app.models.document_types import DocumentType, IngestPathway, get_pathway
from app.models.llm_schemas import ClassificationResponse
from app.services.nit_utils import normalize_optional_nit
from pydantic import BaseModel, Field


def _nit_root(value: str | None) -> str | None:
    """Normalize a NIT and drop the verification digit for equality checks.

    `normalize_optional_nit` preserves the hyphen and DV; here we strip both
    so "901016386" and "901016386-7" compare equal — common when one source
    quotes the DV and another does not.
    """
    normalized = normalize_optional_nit(value)
    if not normalized:
        return None
    return normalized.split("-", 1)[0]


def _normalize_name(value: str | None) -> str:
    """Lowercase + NFKD accent-fold + collapse whitespace for fuzzy name match.

    Used by the factura_venta/factura_compra direction override to compare
    emisor_extracted vs company_name without false negatives from accents
    ("CORPORACIÓN" vs "Corporacion") or punctuation variance.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(folded.lower().split())


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
    direction_signal: Optional[str] = Field(
        default=None,
        description=(
            "For factura_venta/factura_compra: signal that determined the "
            "direction (nit_match_emisor, nit_match_adquirente, "
            "name_match_venta, name_match_compra, default_compra, "
            "override_no_nit_evidence, override_emisor_mismatch). "
            "Surfaced for audit traces and frontend debug."
        ),
    )
    emisor_extracted: Optional[str] = Field(
        default=None,
        description=(
            "Razón social del emisor literalmente extraída del cuerpo del "
            "documento por el LLM. Null si redactado/ausente."
        ),
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
        emisor_norm = _normalize_name(emisor_extracted)
        company_norm = _normalize_name(company_name)
        # When ``emisor_extracted`` is None the LLM honestly admitted it could
        # not read the razón social from the document — treat as no signal
        # (the LLM is instructed by CLASSIFICATION_PROMPT to NOT hallucinate
        # the legal suffix or echo the NIT into this field). Mismatch only
        # fires when we have real names on both sides and they truly differ
        # by accent-folded substring match.
        emisor_mismatch = (
            bool(emisor_norm)
            and bool(company_norm)
            and company_norm not in emisor_norm
            and emisor_norm not in company_norm
        )
        # Short-circuit: when the LLM extracted a NIT that matches the
        # company's NIT, that is authoritative evidence the emisor IS us.
        # Skip the emisor_extracted substring check — it's a weaker signal
        # and produces false positives when the razón social on the doc is
        # redacted/truncated (e.g. only "SAS" survives the OCR crop).
        entity_nit_root = _nit_root(entity_nit_value)
        company_nit_root = _nit_root(company_nit)
        # NIT-match is authoritative: when the LLM explicitly compared the
        # emisor NIT against the tenant NIT and they match, trust it. The
        # CLASSIFICATION_PROMPT instructs the LLM to return ``entity_nit=None``
        # when the printed NIT is redacted/blurred instead of hallucinating
        # it, so reaching this branch with both NITs present means we have
        # real evidence.
        nit_match_authoritative = (
            direction_signal == "nit_match_emisor"
            and entity_nit_root is not None
            and company_nit_root is not None
            and entity_nit_root == company_nit_root
        )
        if nit_match_authoritative and emisor_mismatch:
            logger.info(
                "doc_classifier: NIT-match authoritative; skipping emisor "
                "substring override (entity_nit=%s, company_nit=%s, "
                "emisor_extracted=%s, company_name=%s)",
                entity_nit_value,
                company_nit or "—",
                emisor_extracted or "—",
                company_name or "—",
            )

        # DIAN header authoritative: if the document literally shows the
        # representación gráfica "Factura Electrónica de Venta" (or compra),
        # trust the printed title over multi-tenant direction logic. This
        # matches Sam's requirement: any FV uploaded under any company must
        # classify as factura_venta. Manual CE-style purchase comprobantes
        # (e.g. FRA COUNTRY) lack the DIAN header so they fall through and
        # the emisor_mismatch override still applies.
        folded_preview = _normalize_name(text_preview)
        has_dian_venta_header = "factura electronica de venta" in folded_preview
        has_dian_compra_header = "factura electronica de compra" in folded_preview
        header_authoritative = False
        if has_dian_venta_header and doc_type in (
            DocumentType.FACTURA_VENTA,
            DocumentType.FACTURA_COMPRA,
        ):
            if emisor_mismatch:
                # Multi-tenant: the doc IS a DIAN factura_venta from the
                # issuer's perspective, but the emisor name clearly differs
                # from our tenant — so OUR tenant is the adquirente and the
                # document goes into OUR books as a factura_compra.
                if doc_type != DocumentType.FACTURA_COMPRA:
                    logger.info(
                        "doc_classifier: DIAN venta header + emisor_mismatch "
                        "(emisor=%s, tenant=%s) — flipping to factura_compra",
                        emisor_extracted or "—",
                        company_name or "—",
                    )
                doc_type = DocumentType.FACTURA_COMPRA
                pathway = get_pathway(doc_type)
                direction_signal = "header_venta_emisor_mismatch_compra"
                header_authoritative = True
            else:
                if doc_type != DocumentType.FACTURA_VENTA:
                    logger.info(
                        "doc_classifier: DIAN 'Factura Electrónica de Venta' "
                        "header authoritative — forcing factura_venta (was %s)",
                        doc_type.value,
                    )
                doc_type = DocumentType.FACTURA_VENTA
                pathway = get_pathway(doc_type)
                direction_signal = "header_factura_venta"
                header_authoritative = True
        elif has_dian_compra_header and doc_type in (
            DocumentType.FACTURA_VENTA,
            DocumentType.FACTURA_COMPRA,
        ):
            if doc_type != DocumentType.FACTURA_COMPRA:
                logger.info(
                    "doc_classifier: DIAN 'Factura Electrónica de Compra' "
                    "header authoritative — forcing factura_compra (was %s)",
                    doc_type.value,
                )
            doc_type = DocumentType.FACTURA_COMPRA
            pathway = get_pathway(doc_type)
            direction_signal = "header_factura_compra"
            header_authoritative = True

        if (
            doc_type == DocumentType.FACTURA_VENTA
            and not nit_match_authoritative
            and not header_authoritative
            and (
                (direction_signal == "nit_match_emisor" and not entity_nit_value)
                or emisor_mismatch
            )
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
            direction_signal=direction_signal,
            emisor_extracted=emisor_extracted,
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
