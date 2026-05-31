"""HITL period review on /api/v1/ingest/{id}/period.

The Vía B uploads land in ``financial_statements`` with an LLM-extracted
period range and frequency. When the extraction is shaky (low confidence,
collapsed range, span-inferred frequency, or any annual closing), the
``IngestDetailResponse`` surfaces a ``period_review`` block so the contador
can verify and edit before downstream NIC 7 derivation consumes the row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from main import app


@pytest.fixture
def client():
    mock_db = MagicMock()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app, raise_server_exceptions=False), mock_db
    app.dependency_overrides.clear()


def _stmt_mock(
    *,
    ingest_id: str,
    statement_type: str = "balance_general",
    period_start: datetime,
    period_end: datetime,
    frequency: str | None = None,
    data: dict | None = None,
) -> MagicMock:
    """Build a mock FinancialStatement row matching the SA model surface."""
    stmt = MagicMock()
    stmt.id = "fs_test"
    stmt.ingest_id = ingest_id
    stmt.statement_type = statement_type
    stmt.entity_nit = "800999888"
    stmt.period_start = period_start
    stmt.period_end = period_end
    stmt.frequency = frequency
    stmt.data = data if data is not None else {}
    return stmt


class TestPatchPeriod:
    def test_period_end_before_start_returns_400(self, client):
        c, _db = client
        rsp = c.patch(
            "/api/v1/ingest/ing_x/period",
            json={"period_start": "2025-12-31", "period_end": "2025-01-01"},
        )
        # 404 if the ingest job doesn't exist OR 400 for invalid range; the
        # validation runs before the job lookup so we expect 400 here.
        assert rsp.status_code == 400
        assert "period_end" in rsp.json()["detail"]

    def test_unknown_ingest_returns_404(self, client):
        c, _db = client
        with patch("app.api.v1.ingest.db_service.get_ingest_job", return_value=None):
            rsp = c.patch(
                "/api/v1/ingest/ing_missing/period",
                json={
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "periodicidad": "anual",
                },
            )
        assert rsp.status_code == 404

    def test_override_updates_statement_and_writes_audit(self, client):
        """Happy path: PATCH updates the FinancialStatement and creates an audit row."""
        c, db = client

        ingest_job = MagicMock()
        ingest_job.id = "ing_x"
        stmt = _stmt_mock(
            ingest_id="ing_x",
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
            frequency="monthly",
            data={"periodo_fin": "2026-01-31"},
        )

        # The endpoint queries FinancialStatement via a chained ORM call; map
        # it to our mock row.
        db_query = db.query.return_value
        db_query.filter.return_value = db_query
        db_query.order_by.return_value = db_query
        db_query.first.return_value = stmt

        with (
            patch(
                "app.api.v1.ingest.db_service.get_ingest_job", return_value=ingest_job
            ),
            patch("app.api.v1.ingest.db_service.create_audit_log") as mock_audit,
            patch(
                "app.api.v1.ingest._build_ingest_detail_response",
                return_value={
                    "ingest_id": "ing_x",
                    "file_name": "x.pdf",
                    "status": "completed",
                    "raw_transactions": [],
                },
            ),
        ):
            rsp = c.patch(
                "/api/v1/ingest/ing_x/period",
                json={
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "periodicidad": "anual",
                },
            )

        assert rsp.status_code == 200
        # period_start, period_end, frequency must have been overwritten on the
        # FinancialStatement row.
        assert stmt.period_start.year == 2025
        assert stmt.period_end.year == 2025
        assert stmt.frequency == "annual"
        # Audit log call: action + entity_id + the previous/new payload.
        mock_audit.assert_called_once()
        kwargs = mock_audit.call_args.kwargs
        assert kwargs["action"] == "period_review_override"
        assert kwargs["entity_id"] == "fs_test"
        assert kwargs["details"]["previous"]["frequency"] == "monthly"
        assert kwargs["details"]["new"]["frequency"] == "annual"

    def test_returns_404_when_no_financial_statement(self, client):
        """PATCH against a Vía A ingest (no FinancialStatement) returns 404."""
        c, db = client

        ingest_job = MagicMock()
        ingest_job.id = "ing_via_a"
        db_query = db.query.return_value
        db_query.filter.return_value = db_query
        db_query.order_by.return_value = db_query
        db_query.first.return_value = None

        with patch(
            "app.api.v1.ingest.db_service.get_ingest_job", return_value=ingest_job
        ):
            rsp = c.patch(
                "/api/v1/ingest/ing_via_a/period",
                json={
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                },
            )
        assert rsp.status_code == 404
        assert "estado financiero" in rsp.json()["detail"].lower()


class TestPeriodReviewSurface:
    """The ``period_review`` block appears on IngestDetailResponse when the
    extraction needs human verification."""

    def test_annual_balance_triggers_review(self):
        from app.api.v1.ingest import _build_period_review

        job = MagicMock()
        job.id = "ing_annual"
        job.classification_confidence = 0.95  # high confidence
        stmt = _stmt_mock(
            ingest_id="ing_annual",
            period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
            frequency="annual",
            data={"periodicidad": "anual"},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            stmt
        )

        review = _build_period_review(db, job)
        assert review is not None
        # Annual closings always trigger review (high-leverage for derivation).
        assert review["review_reason"] == "annual_high_value"
        assert review["extracted_periodicidad"] == "annual"
        assert review["requires_review"] is True

    def test_low_confidence_triggers_review(self):
        from app.api.v1.ingest import _build_period_review

        job = MagicMock()
        job.id = "ing_low_conf"
        job.classification_confidence = 0.6
        stmt = _stmt_mock(
            ingest_id="ing_low_conf",
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
            frequency="monthly",
            data={"periodicidad": "mensual"},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            stmt
        )

        review = _build_period_review(db, job)
        assert review is not None
        assert review["review_reason"] == "low_confidence"
        assert review["extraction_confidence"] == 0.6

    def test_high_confidence_monthly_no_review(self):
        """Monthly with clean LLM-extracted periodicidad and high confidence
        doesn't bother the user."""
        from app.api.v1.ingest import _build_period_review

        job = MagicMock()
        job.id = "ing_clean"
        job.classification_confidence = 0.95
        stmt = _stmt_mock(
            ingest_id="ing_clean",
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
            frequency="monthly",
            data={"periodicidad": "mensual"},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            stmt
        )

        review = _build_period_review(db, job)
        assert review is None

    def test_no_statement_returns_none(self):
        """Vía A ingests (no FinancialStatement row) skip the review entirely."""
        from app.api.v1.ingest import _build_period_review

        job = MagicMock()
        job.id = "ing_via_a"
        job.classification_confidence = 0.95
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        assert _build_period_review(db, job) is None
