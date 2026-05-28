"""Tests for draft → reviewed → filed workflow endpoints.

Covers:
- POST /declarations/{id}/review
- POST /declarations/{id}/file
- POST /declarations/{id}/reopen
- PATCH /declarations/{id}/fields — locked when non-draft
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def override_auth():
    from uuid import UUID

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        email="contador@test.com",
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _make_draft(status="draft", fields=None):
    """Build a mock TaxDeclarationDraft ORM object."""
    from datetime import datetime

    draft = MagicMock()
    draft.id = "draft-001"
    draft.company_nit = "800999888"
    draft.form_type = "F300"
    draft.period_start = "2026-01-01"
    draft.period_end = "2026-01-31"
    draft.year = 2026
    draft.status = status
    draft.fields_json = fields if fields is not None else []
    draft.warnings_json = []
    draft.created_at = datetime(2026, 1, 1, 12, 0, 0)
    draft.updated_at = datetime(2026, 1, 1, 12, 0, 0)
    draft.reviewed_by = None
    draft.reviewed_at = None
    draft.filed_by = None
    draft.filed_at = None
    draft.dian_acknowledgment = None
    draft.reopened_by = None
    draft.reopened_at = None
    draft.reopen_reason = None
    return draft


# ---------------------------------------------------------------------------
# POST /review
# ---------------------------------------------------------------------------


class TestReviewDraft:
    def test_review_success(self, client):
        """Draft with no pending fields → status=reviewed."""
        draft = _make_draft(
            status="draft",
            fields=[{"renglon": "1", "value": 100.0, "requires_review": False}],
        )
        with (
            patch("app.api.v1.tax.get_draft", return_value=draft),
            patch("app.core.database.get_db"),
        ):
            app.dependency_overrides[get_db] = lambda: MagicMock()
            rsp = client.post("/api/v1/tax/declarations/draft-001/review")
        assert rsp.status_code == 200
        # draft.status was mutated by endpoint
        assert draft.status == "reviewed"
        assert draft.reviewed_by == "contador@test.com"

    def test_review_with_pending_fields_returns_400(self, client):
        """Fields still requiring review → 400 FIELDS_PENDING_REVIEW."""
        fields = [
            {"renglon": "1", "value": 0.0, "requires_review": True},
            {"renglon": "2", "value": 0.0, "requires_review": True},
        ]
        draft = _make_draft(status="draft", fields=fields)
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post("/api/v1/tax/declarations/draft-001/review")
        assert rsp.status_code == 400
        body = rsp.json()
        assert body["detail"]["error_code"] == "FIELDS_PENDING_REVIEW"
        assert body["detail"]["count"] == 2

    def test_review_non_draft_returns_400(self, client):
        """Already reviewed → 400."""
        draft = _make_draft(status="reviewed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post("/api/v1/tax/declarations/draft-001/review")
        assert rsp.status_code == 400
        assert "borradores" in rsp.json()["detail"]

    def test_review_not_found_returns_404(self, client):
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=None):
            rsp = client.post("/api/v1/tax/declarations/missing/review")
        assert rsp.status_code == 404


# ---------------------------------------------------------------------------
# POST /file
# ---------------------------------------------------------------------------


class TestFileDraft:
    def test_file_from_reviewed_success(self, client):
        """reviewed → filed, dian_acknowledgment persisted."""
        draft = _make_draft(status="reviewed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post(
                "/api/v1/tax/declarations/draft-001/file",
                json={"dian_acknowledgment": "900123456789"},
            )
        assert rsp.status_code == 200
        assert draft.status == "filed"
        assert draft.filed_by == "contador@test.com"
        assert draft.dian_acknowledgment == "900123456789"

    def test_file_from_draft_returns_400(self, client):
        """Cannot file a draft directly."""
        draft = _make_draft(status="draft")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post("/api/v1/tax/declarations/draft-001/file", json={})
        assert rsp.status_code == 400
        assert "revisadas" in rsp.json()["detail"]

    def test_file_without_acknowledgment_ok(self, client):
        """dian_acknowledgment is optional."""
        draft = _make_draft(status="reviewed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post("/api/v1/tax/declarations/draft-001/file", json={})
        assert rsp.status_code == 200
        assert draft.status == "filed"


# ---------------------------------------------------------------------------
# POST /reopen
# ---------------------------------------------------------------------------


class TestReopenDraft:
    def test_reopen_from_filed_goes_to_reviewed(self, client):
        """filed → reviewed; filed fields cleared; audit fields set."""
        from datetime import datetime

        draft = _make_draft(status="filed")
        draft.filed_at = datetime(2026, 2, 1)
        draft.filed_by = "admin@test.com"
        draft.dian_acknowledgment = "12345"
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post(
                "/api/v1/tax/declarations/draft-001/reopen",
                json={"reason": "Error en el formulario"},
            )
        assert rsp.status_code == 200
        assert draft.status == "reviewed"
        assert draft.filed_at is None
        assert draft.filed_by is None
        assert draft.dian_acknowledgment is None
        assert draft.reopened_by == "contador@test.com"
        assert draft.reopen_reason == "Error en el formulario"

    def test_reopen_from_reviewed_goes_to_draft(self, client):
        """reviewed → draft; reviewed fields cleared."""
        from datetime import datetime

        draft = _make_draft(status="reviewed")
        draft.reviewed_at = datetime(2026, 2, 1)
        draft.reviewed_by = "admin@test.com"
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post(
                "/api/v1/tax/declarations/draft-001/reopen",
                json={"reason": "Ajuste de cifras"},
            )
        assert rsp.status_code == 200
        assert draft.status == "draft"
        assert draft.reviewed_at is None
        assert draft.reviewed_by is None

    def test_reopen_from_draft_returns_400(self, client):
        """Draft already editable → 400."""
        draft = _make_draft(status="draft")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post(
                "/api/v1/tax/declarations/draft-001/reopen",
                json={"reason": "No debería llegar aquí"},
            )
        assert rsp.status_code == 400
        assert "editable" in rsp.json()["detail"]

    def test_reopen_without_reason_returns_400(self, client):
        """reason is required."""
        draft = _make_draft(status="reviewed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post("/api/v1/tax/declarations/draft-001/reopen", json={})
        assert rsp.status_code == 422

    def test_reopen_reason_too_short_returns_422(self, client):
        """reason min_length=5."""
        draft = _make_draft(status="reviewed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.post(
                "/api/v1/tax/declarations/draft-001/reopen",
                json={"reason": "ok"},
            )
        assert rsp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /fields — locked when non-draft
# ---------------------------------------------------------------------------


class TestPatchFieldsLocked:
    def test_patch_blocked_when_reviewed(self, client):
        """PATCH → 400 DRAFT_LOCKED when status=reviewed."""
        draft = _make_draft(status="reviewed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.patch(
                "/api/v1/tax/declarations/draft-001/fields",
                json={"renglon": "1", "value": 500.0},
            )
        assert rsp.status_code == 400
        body = rsp.json()
        assert body["detail"]["error_code"] == "DRAFT_LOCKED"
        assert "reviewed" in body["detail"]["message"]

    def test_patch_blocked_when_filed(self, client):
        """PATCH → 400 DRAFT_LOCKED when status=filed."""
        draft = _make_draft(status="filed")
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("app.api.v1.tax.get_draft", return_value=draft):
            rsp = client.patch(
                "/api/v1/tax/declarations/draft-001/fields",
                json={"renglon": "1", "value": 500.0},
            )
        assert rsp.status_code == 400
        body = rsp.json()
        assert body["detail"]["error_code"] == "DRAFT_LOCKED"
        assert "filed" in body["detail"]["message"]
