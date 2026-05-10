"""Unit tests for app.agents.routing.ingest_router."""

from unittest.mock import MagicMock, patch

import pytest

from app.agents.routing.ingest_router import route
from app.models.document_types import DocumentType, IngestPathway


def _make_state(**kwargs):
    defaults = {
        "ingest_id": "test-ingest-123",
        "file_path": "/tmp/test.pdf",
        "agent_log": [],
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.unit
class TestIngestRouter:
    @patch("app.agents.routing.ingest_router.Path")
    @patch("app.agents.routing.ingest_router.extract_preview")
    def test_route_file_not_found(self, mock_extract_preview, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path_cls.return_value = mock_path_instance

        state = _make_state(file_path="/tmp/missing.pdf")
        result = route(state)

        assert result["error"] == "File not found: /tmp/missing.pdf"
        assert result["agent_log"][-1]["event"] == "routing_error"
        mock_extract_preview.assert_not_called()

    @patch("app.agents.routing.ingest_router.Path")
    @patch("app.agents.routing.ingest_router.extract_preview")
    def test_route_unsupported_format(self, mock_extract_preview, mock_path_cls):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.suffix.lower.return_value = ".txt"
        mock_path_instance.name.lower.return_value = "test.txt"
        mock_path_cls.return_value = mock_path_instance

        state = _make_state(file_path="/tmp/test.txt")
        result = route(state)

        assert "Unsupported file type" in result["error"]
        assert result["agent_log"][-1]["event"] == "routing_error"
        mock_extract_preview.assert_not_called()

    @patch("app.agents.routing.ingest_router.SessionLocal")
    @patch("app.agents.routing.ingest_router.db_service.get_ingest_job")
    @patch("app.agents.routing.ingest_router.load_confirmed_classification")
    @patch("app.agents.routing.ingest_router.Path")
    @patch("app.agents.routing.ingest_router.extract_preview")
    def test_route_confirmed_classification_via_b(
        self,
        mock_extract_preview,
        mock_path_cls,
        mock_load_confirmed,
        mock_get_ingest_job,
        mock_session_local,
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.suffix.lower.return_value = ".pdf"
        mock_path_instance.name.lower.return_value = "test.pdf"
        mock_path_cls.return_value = mock_path_instance

        mock_extract_preview.return_value = ("preview text", None)

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_job = MagicMock()
        mock_job.classification_confirmed = True
        mock_get_ingest_job.return_value = mock_job

        mock_load_confirmed.return_value = {
            "doc_type": DocumentType.BALANCE_GENERAL.value,
            "pathway": IngestPathway.WORK_WITH_EXISTING.value,
        }

        state = _make_state(
            file_path="/tmp/test.pdf",
            pathway=IngestPathway.WORK_WITH_EXISTING.value,
        )
        result = route(state)

        assert result["current_agent"] == "ingesta"
        assert result["mode"] == "ingest"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["next_agent"] == "ingesta"

    @patch("app.services.doc_classifier.classify_document")
    @patch("app.agents.routing.ingest_router.SessionLocal")
    @patch("app.agents.routing.ingest_router.db_service.get_ingest_job")
    @patch("app.agents.routing.ingest_router.load_confirmed_classification")
    @patch("app.agents.routing.ingest_router.Path")
    @patch("app.agents.routing.ingest_router.extract_preview")
    def test_route_via_b_forces_build_for_extracto(
        self,
        mock_extract_preview,
        mock_path_cls,
        mock_load_confirmed,
        mock_get_ingest_job,
        mock_session_local,
        mock_classify_document,
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.suffix.lower.return_value = ".xlsx"
        mock_path_instance.name.lower.return_value = "extracto_bancario.xlsx"
        mock_path_cls.return_value = mock_path_instance

        mock_extract_preview.return_value = ("preview text", None)

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_job = MagicMock()
        mock_get_ingest_job.return_value = mock_job

        mock_load_confirmed.return_value = None

        mock_classification = MagicMock()
        mock_classification.error = None
        mock_classification.doc_type = DocumentType.EXTRACTO_BANCARIO
        mock_classification.pathway = IngestPathway.WORK_WITH_EXISTING
        mock_classification.confidence = 0.95
        mock_classification.model_dump.return_value = {
            "doc_type": DocumentType.EXTRACTO_BANCARIO.value,
            "pathway": IngestPathway.WORK_WITH_EXISTING.value,
            "confidence": 0.95,
            "entity_nit": None,
        }
        mock_classify_document.return_value = mock_classification

        state = _make_state(file_path="/tmp/extracto_bancario.xlsx", ingest_id="")
        result = route(state)

        assert result["pathway"] == IngestPathway.BUILD_FROM_SCRATCH.value
        assert (
            result["document_classification"]["doc_type"]
            == DocumentType.EXTRACTO_BANCARIO.value
        )
        assert result["current_agent"] == "ingesta"

    @patch("app.services.doc_classifier.classify_document")
    @patch("app.agents.routing.ingest_router.SessionLocal")
    @patch("app.agents.routing.ingest_router.db_service.get_ingest_job")
    @patch("app.agents.routing.ingest_router.load_confirmed_classification")
    @patch("app.agents.routing.ingest_router.Path")
    @patch("app.agents.routing.ingest_router.extract_preview")
    def test_route_unconfirmed_goes_pending_review(
        self,
        mock_extract_preview,
        mock_path_cls,
        mock_load_confirmed,
        mock_get_ingest_job,
        mock_session_local,
        mock_classify_document,
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.suffix.lower.return_value = ".pdf"
        mock_path_instance.name.lower.return_value = "test.pdf"
        mock_path_cls.return_value = mock_path_instance

        mock_extract_preview.return_value = ("preview text", None)

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_job = MagicMock()
        mock_get_ingest_job.return_value = mock_job

        mock_load_confirmed.return_value = None

        mock_classification = MagicMock()
        mock_classification.error = None
        mock_classification.doc_type = DocumentType.FACTURA_VENTA
        mock_classification.pathway = IngestPathway.BUILD_FROM_SCRATCH
        mock_classification.confidence = 0.75
        mock_classification.model_dump.return_value = {
            "doc_type": DocumentType.FACTURA_VENTA.value,
            "pathway": IngestPathway.BUILD_FROM_SCRATCH.value,
            "confidence": 0.75,
            "entity_nit": None,
        }
        mock_classify_document.return_value = mock_classification

        state = _make_state(file_path="/tmp/test.pdf")
        result = route(state)

        assert result["current_agent"] == "review_terminal"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["next_agent"] == "review_terminal"

    @patch("app.agents.routing.ingest_router.SessionLocal")
    @patch("app.agents.routing.ingest_router.db_service.get_ingest_job")
    @patch("app.agents.routing.ingest_router.load_confirmed_classification")
    @patch("app.agents.routing.ingest_router.Path")
    @patch("app.agents.routing.ingest_router.extract_preview")
    def test_route_via_a_routes_to_ingesta(
        self,
        mock_extract_preview,
        mock_path_cls,
        mock_load_confirmed,
        mock_get_ingest_job,
        mock_session_local,
    ):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.suffix.lower.return_value = ".pdf"
        mock_path_instance.name.lower.return_value = "test.pdf"
        mock_path_cls.return_value = mock_path_instance

        mock_extract_preview.return_value = ("preview text", None)

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_job = MagicMock()
        mock_get_ingest_job.return_value = mock_job

        mock_load_confirmed.return_value = {
            "doc_type": DocumentType.FACTURA_VENTA.value,
            "pathway": IngestPathway.BUILD_FROM_SCRATCH.value,
        }

        state = _make_state(
            file_path="/tmp/test.pdf",
            pathway=IngestPathway.BUILD_FROM_SCRATCH.value,
        )
        result = route(state)

        assert result["current_agent"] == "ingesta"
        assert result["mode"] == "ingest"
        assert result["agent_log"][-1]["event"] == "routing_complete"
        assert result["agent_log"][-1]["details"]["next_agent"] == "ingesta"
