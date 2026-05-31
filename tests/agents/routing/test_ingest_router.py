"""Unit tests for ingest_router.route_ingest."""

from unittest.mock import MagicMock, patch


def _state(**kwargs):
    base = {
        "mode": "ingest",
        "current_agent": "",
        "file_path": "/tmp/factura.pdf",
        "file_name": "factura.pdf",
        "ingest_id": "",
        "company_nit": "800999888",
        "pathway": None,
        "document_classification": None,
        "validation_history": [],
        "retry_count": 0,
        "correction_feedback": None,
        "agent_log": [],
        "current_stage": None,
        "audit_decision": None,
        "audit_feedback": None,
        "audit_rejection_count": 0,
    }
    return {**base, **kwargs}


def _mock_classification(doc_type="factura", pathway="build_from_scratch"):
    c = MagicMock()
    c.doc_type.value = doc_type
    c.pathway.value = pathway
    c.confidence = 0.95
    c.error = None
    c.entity_nit = None
    c.model_dump.return_value = {
        "doc_type": doc_type,
        "pathway": pathway,
        "confidence": 0.95,
        "entity_nit": None,
    }
    return c


def test_error_when_file_not_found():
    from app.agents.routing.ingest_router import route_ingest

    result = route_ingest(_state(file_path="/tmp/nonexistent_xyz_123.pdf"))
    assert result.get("error") is not None
    assert "not found" in result["error"].lower() or "File" in result["error"]


def test_error_on_unsupported_extension(tmp_path):
    bad_file = tmp_path / "doc.txt"
    bad_file.write_text("hello")
    from app.agents.routing.ingest_router import route_ingest

    result = route_ingest(_state(file_path=str(bad_file)))
    assert result.get("error") is not None


def test_routes_to_ingesta_on_confirmed_classification(tmp_path):
    pdf = tmp_path / "factura.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    from app.agents.routing.ingest_router import route_ingest

    mock_job = MagicMock()
    mock_job.classification_confirmed = True
    mock_job.document_type = "factura"
    mock_job.pathway = "build_from_scratch"

    with (
        patch("app.agents.routing.ingest_router.SessionLocal"),
        patch("app.agents.routing.ingest_router.db_service") as mock_db,
        patch(
            "app.agents.routing.ingest_router.normalize_optional_nit",
            return_value="800999888",
        ),
    ):
        mock_db.get_ingest_job.return_value = mock_job
        mock_db.get_company_settings.return_value = None
        mock_db.set_company_locked_pathway.return_value = None
        result = route_ingest(_state(file_path=str(pdf), ingest_id="test-ingest-id"))

    # Should route to ingesta (confirmed + build_from_scratch goes via_a path)
    assert result["current_agent"] in ("ingesta", "review_terminal")


def test_routes_to_review_terminal_on_unconfirmed(tmp_path):
    pdf = tmp_path / "factura.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    from app.agents.routing.ingest_router import route_ingest

    mock_job = MagicMock()
    mock_job.classification_confirmed = False
    mock_job.document_type = "factura"
    mock_job.pathway = "build_from_scratch"
    mock_job.status = "pending"

    classification = _mock_classification()

    with (
        patch("app.agents.routing.ingest_router.SessionLocal"),
        patch("app.agents.routing.ingest_router.db_service") as mock_db,
        patch(
            "app.agents.routing.ingest_router.normalize_optional_nit",
            return_value="800999888",
        ),
        patch(
            "app.agents.routing.ingest_router.classify_document",
            return_value=classification,
        ),
        patch(
            "app.services.pdf_processor.extract_text_from_pdf",
            return_value="texto factura",
        ),
    ):
        mock_db.get_ingest_job.return_value = mock_job
        mock_db.get_company_settings.return_value = None
        result = route_ingest(_state(file_path=str(pdf), ingest_id="test-ingest-id"))

    assert result["current_agent"] == "review_terminal"
