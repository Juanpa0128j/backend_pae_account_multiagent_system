"""Classification persistence helpers for the ingest router.

Thin adapters around db_service to keep DB logic out of the router.
"""

from decimal import Decimal
from typing import Optional

from app.core.logger import get_logger
from app.models.database import IngestStatus
from app.services import db_service

logger = get_logger("app.services.classification_persistence")


def load_confirmed_classification(ingest_job) -> Optional[dict]:
    """Load classification from a confirmed ingest job."""
    use_confirmed = bool(
        ingest_job and getattr(ingest_job, "classification_confirmed", False)
    )
    if not use_confirmed:
        return None

    doc_type_value = str(ingest_job.document_type or "").strip()
    pathway_value = str(ingest_job.pathway or "").strip()

    if doc_type_value and not pathway_value:
        from app.models.document_types import DocumentType, get_pathway

        try:
            pathway_value = get_pathway(DocumentType(doc_type_value)).value
        except ValueError:
            logger.warning(
                "classification_persistence: invalid confirmed doc_type '%s'",
                doc_type_value,
            )
            return None

    result = {"doc_type": doc_type_value}
    if pathway_value:
        result["pathway"] = pathway_value
    return result


def save_classification_metadata(db, ingest_id: str, classification) -> None:
    """Persist classification metadata to the ingest job."""
    ingest_job = db_service.get_ingest_job(db, ingest_id)
    if not ingest_job:
        return

    current_status = ingest_job.status
    if not isinstance(current_status, IngestStatus):
        current_status = IngestStatus(str(current_status))

    db_service.update_ingest_job(
        db,
        ingest_id,
        current_status,
        document_type=classification.doc_type.value,
        pathway=classification.pathway.value,
        classification_confidence=Decimal(str(classification.confidence)),
    )


def mark_pending_review(
    db,
    ingest_id: str,
    doc_type: Optional[str],
    pathway: Optional[str],
    confidence: Optional[float],
) -> None:
    """Mark ingest job as pending review."""
    db_service.update_ingest_job(
        db,
        ingest_id,
        IngestStatus.PENDING_REVIEW,
        document_type=doc_type,
        pathway=pathway,
        classification_confirmed=False,
        classification_confidence=Decimal(str(confidence)) if confidence else None,
    )
