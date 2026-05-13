"""Tests for Via A/B mutual exclusion — get/set_company_locked_pathway and upload enforcement."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.services import db_service

# ---------------------------------------------------------------------------
# db_service unit tests
# ---------------------------------------------------------------------------


class TestGetCompanyLockedPathway:
    def test_returns_none_when_company_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = db_service.get_company_locked_pathway(db, "999999999")
        assert result is None

    def test_returns_none_when_pathway_not_set(self):
        db = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda self, i: None  # row[0] = None
        db.query.return_value.filter.return_value.first.return_value = (None,)
        result = db_service.get_company_locked_pathway(db, "800999888")
        assert result is None

    def test_returns_locked_pathway_when_set(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = (
            "build_from_scratch",
        )
        result = db_service.get_company_locked_pathway(db, "800999888")
        assert result == "build_from_scratch"


class TestSetCompanyLockedPathway:
    """The new implementation issues an atomic conditional UPDATE
    (`WHERE locked_pathway IS NULL`) so concurrent first uploads can't race.
    Mocks must reflect that — `db.query(...).filter(...).update(...)` returns
    the rowcount, and commit only happens when a row was actually updated.
    """

    def test_sets_pathway_when_not_locked(self):
        db = MagicMock()
        # Conditional UPDATE matched the NULL row → 1 row affected.
        db.query.return_value.filter.return_value.update.return_value = 1

        db_service.set_company_locked_pathway(db, "800999888", "work_with_existing")

        db.query.return_value.filter.return_value.update.assert_called_once()
        db.commit.assert_called_once()

    def test_noop_when_already_locked(self):
        db = MagicMock()
        # Conditional UPDATE skipped because locked_pathway IS NOT NULL.
        db.query.return_value.filter.return_value.update.return_value = 0

        db_service.set_company_locked_pathway(db, "800999888", "work_with_existing")

        db.commit.assert_not_called()

    def test_noop_when_company_not_found(self):
        db = MagicMock()
        # Conditional UPDATE matched no rows.
        db.query.return_value.filter.return_value.update.return_value = 0

        db_service.set_company_locked_pathway(db, "999999999", "build_from_scratch")

        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Upload endpoint enforcement tests
# ---------------------------------------------------------------------------


class TestUploadPathwayEnforcement:
    """Test 409 enforcement in the upload endpoint when company is locked."""

    def _make_upload_request(
        self, client, doc_type: str = None, company_nit: str = "800999888"
    ):
        import io

        fake_pdf = b"%PDF-1.4 fake content"
        data = {"company_nit": company_nit}
        if doc_type:
            data["doc_type"] = doc_type
        return client.post(
            "/api/v1/ingest/upload",
            files=[("files", ("test.pdf", io.BytesIO(fake_pdf), "application/pdf"))],
            data=data,
        )

    def test_via_b_upload_blocked_when_locked_to_via_a(self):
        # Via B upload (doc_type=balance_general) → company locked to Via A → 409
        from main import app

        with TestClient(app) as client:
            with (
                patch(
                    "app.api.v1.ingest.db_service.get_company_locked_pathway"
                ) as mock_lock,
                patch("app.api.v1.ingest.db_service.create_ingest_job") as mock_create,
                patch("app.api.v1.ingest.db_service.set_company_locked_pathway"),
            ):
                mock_lock.return_value = "build_from_scratch"
                response = self._make_upload_request(client, doc_type="balance_general")

        assert response.status_code == 409
        assert "Vía A" in response.json()["detail"]
        mock_create.assert_not_called()

    def test_via_a_upload_blocked_when_locked_to_via_b(self):
        # Via A upload (no doc_type = unclassified) → company locked to Via B → 409
        from main import app

        with TestClient(app) as client:
            with (
                patch(
                    "app.api.v1.ingest.db_service.get_company_locked_pathway"
                ) as mock_lock,
                patch("app.api.v1.ingest.db_service.create_ingest_job") as mock_create,
                patch("app.api.v1.ingest.db_service.set_company_locked_pathway"),
            ):
                mock_lock.return_value = "work_with_existing"
                response = self._make_upload_request(client)  # no doc_type = Via A

        assert response.status_code == 409
        assert "Vía B" in response.json()["detail"]
        mock_create.assert_not_called()

    def test_upload_allowed_when_not_locked(self):
        # Via B upload, company has no lock yet → 202
        from main import app

        with TestClient(app) as client:
            with (
                patch(
                    "app.api.v1.ingest.db_service.get_company_locked_pathway"
                ) as mock_lock,
                patch("app.api.v1.ingest.db_service.create_ingest_job") as mock_create,
                patch("app.api.v1.ingest.db_service.set_company_locked_pathway"),
                patch("app.api.v1.ingest.process_ingest_background"),
            ):
                mock_lock.return_value = None
                mock_job = MagicMock()
                mock_job.id = "ing_test"
                mock_job.status.value = "pending_processing"
                mock_job.created_at = None
                mock_create.return_value = mock_job

                response = self._make_upload_request(client, doc_type="balance_general")

        assert response.status_code == 202

    def test_upload_allowed_when_same_pathway(self):
        # Via B upload, company already locked to Via B → 202
        from main import app

        with TestClient(app) as client:
            with (
                patch(
                    "app.api.v1.ingest.db_service.get_company_locked_pathway"
                ) as mock_lock,
                patch("app.api.v1.ingest.db_service.create_ingest_job") as mock_create,
                patch("app.api.v1.ingest.db_service.set_company_locked_pathway"),
                patch("app.api.v1.ingest.process_ingest_background"),
            ):
                mock_lock.return_value = "work_with_existing"
                mock_job = MagicMock()
                mock_job.id = "ing_test"
                mock_job.status.value = "pending_processing"
                mock_job.created_at = None
                mock_create.return_value = mock_job

                response = self._make_upload_request(client, doc_type="balance_general")

        assert response.status_code == 202

    def test_via_a_upload_sets_locked_pathway_when_no_doc_type(self):
        import os
        import tempfile

        from app.agents.supervisor import supervisor_node

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake content")
            temp_path = f.name

        try:
            mock_job = MagicMock()
            mock_job.classification_confirmed = True
            mock_job.document_type = "factura"
            mock_job.pathway = "build_from_scratch"

            mock_db = MagicMock()

            with (
                patch("app.agents.supervisor.SessionLocal", return_value=mock_db),
                patch(
                    "app.agents.supervisor.db_service.get_ingest_job",
                    return_value=mock_job,
                ),
                patch(
                    "app.agents.supervisor.db_service.set_company_locked_pathway"
                ) as mock_set_lock,
                patch(
                    "app.services.pdf_processor.extract_text_from_pdf", return_value=""
                ),
            ):
                state = {
                    "ingest_id": "ing_test",
                    "company_nit": "800999888",
                    "file_path": temp_path,
                    "mode": "ingest",
                }
                result = supervisor_node(state)

            mock_set_lock.assert_called_once_with(
                mock_db, "800999888", "build_from_scratch"
            )
            assert result.get("pathway") == "build_from_scratch"
        finally:
            os.unlink(temp_path)
