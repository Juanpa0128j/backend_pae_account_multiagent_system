from unittest.mock import MagicMock, patch

from app.services.classification_persistence import persist_classification_metadata


def _cls(doc_type="factura", pathway="via_a", confidence=0.95):
    c = MagicMock()
    c.doc_type.value = doc_type
    c.pathway.value = pathway
    c.confidence = confidence
    c.entity_nit = None
    return c


def test_skips_when_no_ingest_id():
    with patch("app.services.classification_persistence.db_service") as mock_svc:
        persist_classification_metadata(MagicMock(), "", _cls())
        mock_svc.update_ingest_job.assert_not_called()


def test_skips_when_job_not_found():
    with patch("app.services.classification_persistence.db_service") as mock_svc:
        mock_svc.get_ingest_job.return_value = None
        persist_classification_metadata(MagicMock(), "ingest-123", _cls())
        mock_svc.update_ingest_job.assert_not_called()


def test_calls_update_when_job_found():
    with patch("app.services.classification_persistence.db_service") as mock_svc:
        mock_svc.get_ingest_job.return_value = MagicMock(status="pending_processing")
        persist_classification_metadata(MagicMock(), "ingest-123", _cls())
        mock_svc.update_ingest_job.assert_called_once()
