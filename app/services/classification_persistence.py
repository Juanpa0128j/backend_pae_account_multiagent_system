"""
Service for persisting document classification metadata to IngestJob.

Extracted from app.agents.supervisor_node (lines ~373-397).
"""

import logging
from decimal import Decimal

from app.models.database import IngestStatus
from app.services import db_service

logger = logging.getLogger(__name__)


def persist_classification_metadata(db, ingest_id: str, classification) -> None:
    """Persist classification metadata to IngestJob so it's visible immediately.

    This is called early in the ingest pipeline so classification results are
    readable via GET /api/v1/ingest/{id} even if downstream ingest fails.

    Args:
        db: SQLAlchemy session.
        ingest_id: UUID string of the IngestJob row. Skips if empty.
        classification: A DocumentClassification model instance with doc_type,
            pathway, and confidence attributes.
    """
    if not ingest_id:
        return
    try:
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
    except Exception as exc:
        logger.warning("Supervisor: failed to persist classification metadata: %s", exc)
