"""Tests for classification_persistence helpers."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models.database import IngestStatus
from app.models.document_types import DocumentType, IngestPathway


@patch("app.services.classification_persistence.db_service")
def test_load_confirmed_classification_confirmed(mock_db_service):
    from app.services.classification_persistence import load_confirmed_classification

    ingest_job = MagicMock()
    ingest_job.classification_confirmed = True
    ingest_job.document_type = DocumentType.FACTURA_VENTA.value
    ingest_job.pathway = IngestPathway.BUILD_FROM_SCRATCH.value

    result = load_confirmed_classification(ingest_job)

    assert result == {
        "doc_type": DocumentType.FACTURA_VENTA.value,
        "pathway": IngestPathway.BUILD_FROM_SCRATCH.value,
    }


@patch("app.models.document_types.get_pathway")
@patch("app.services.classification_persistence.db_service")
def test_load_confirmed_classification_no_pathway(mock_db_service, mock_get_pathway):
    from app.services.classification_persistence import load_confirmed_classification

    mock_get_pathway.return_value = IngestPathway.BUILD_FROM_SCRATCH

    ingest_job = MagicMock()
    ingest_job.classification_confirmed = True
    ingest_job.document_type = DocumentType.FACTURA_VENTA.value
    ingest_job.pathway = None

    result = load_confirmed_classification(ingest_job)

    assert result == {
        "doc_type": DocumentType.FACTURA_VENTA.value,
        "pathway": IngestPathway.BUILD_FROM_SCRATCH.value,
    }
    mock_get_pathway.assert_called_once_with(DocumentType.FACTURA_VENTA)


@patch("app.services.classification_persistence.db_service")
def test_load_confirmed_classification_not_confirmed(mock_db_service):
    from app.services.classification_persistence import load_confirmed_classification

    ingest_job = MagicMock()
    ingest_job.classification_confirmed = False

    result = load_confirmed_classification(ingest_job)

    assert result is None


@patch("app.services.classification_persistence.db_service")
def test_save_classification_metadata(mock_db_service):
    from app.services.classification_persistence import save_classification_metadata

    ingest_job = MagicMock()
    ingest_job.status = IngestStatus.PROCESSING
    mock_db_service.get_ingest_job.return_value = ingest_job

    classification = MagicMock()
    classification.doc_type = DocumentType.FACTURA_VENTA
    classification.pathway = IngestPathway.BUILD_FROM_SCRATCH
    classification.confidence = 0.95

    db = MagicMock()
    save_classification_metadata(db, "ingest-123", classification)

    mock_db_service.get_ingest_job.assert_called_once_with(db, "ingest-123")
    mock_db_service.update_ingest_job.assert_called_once_with(
        db,
        "ingest-123",
        IngestStatus.PROCESSING,
        document_type=DocumentType.FACTURA_VENTA.value,
        pathway=IngestPathway.BUILD_FROM_SCRATCH.value,
        classification_confidence=Decimal("0.95"),
    )


@patch("app.services.classification_persistence.db_service")
def test_mark_pending_review(mock_db_service):
    from app.services.classification_persistence import mark_pending_review

    db = MagicMock()
    mark_pending_review(
        db,
        "ingest-123",
        doc_type=DocumentType.FACTURA_VENTA.value,
        pathway=IngestPathway.BUILD_FROM_SCRATCH.value,
        confidence=0.65,
    )

    mock_db_service.update_ingest_job.assert_called_once_with(
        db,
        "ingest-123",
        IngestStatus.PENDING_REVIEW,
        document_type=DocumentType.FACTURA_VENTA.value,
        pathway=IngestPathway.BUILD_FROM_SCRATCH.value,
        classification_confirmed=False,
        classification_confidence=Decimal("0.65"),
    )
