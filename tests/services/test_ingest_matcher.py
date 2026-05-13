"""Tests for ingest_matcher service."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.database import IngestJob, IngestStatus


@pytest.fixture
def db_engine():
    """Shared in-memory SQLite engine."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db(db_engine):
    """Direct DB session."""
    SessionLocal = sessionmaker(bind=db_engine)
    session = SessionLocal()
    yield session
    session.close()


def _make_ingest_job(
    db: Session,
    job_id: str,
    company_nit: str = "900123456",
    document_type: str | None = "factura_compra",
    status: IngestStatus = IngestStatus.PENDING_PROCESSING,
    created_at: datetime | None = None,
) -> IngestJob:
    job = IngestJob(
        id=job_id,
        file_name="test.pdf",
        company_nit=company_nit,
        document_type=document_type,
        status=status,
        parser_mode="fast",
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    return job


class TestFindMergeCandidates:
    def test_find_merge_candidates_groups_same_type_close_time(self, db: Session):
        """2 jobs, same type, 2 min apart -> grouped."""
        from app.services.ingest_matcher import find_merge_candidates

        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
        _make_ingest_job(db, "ing_001", created_at=now)
        _make_ingest_job(db, "ing_002", created_at=now + timedelta(minutes=2))

        result = find_merge_candidates(db, "900123456", time_window_minutes=5)

        assert len(result) == 1
        assert result[0]["ingest_ids"] == ["ing_001", "ing_002"]
        assert result[0]["document_type"] == "factura_compra"

    def test_find_merge_candidates_excludes_different_types(self, db: Session):
        """2 jobs, different types -> not grouped."""
        from app.services.ingest_matcher import find_merge_candidates

        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
        _make_ingest_job(db, "ing_001", document_type="factura_compra", created_at=now)
        _make_ingest_job(
            db,
            "ing_002",
            document_type="factura_venta",
            created_at=now + timedelta(minutes=1),
        )

        result = find_merge_candidates(db, "900123456", time_window_minutes=5)

        assert len(result) == 0

    def test_find_merge_candidates_excludes_cancelled(self, db: Session):
        """Cancelled job not included."""
        from app.services.ingest_matcher import find_merge_candidates

        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
        _make_ingest_job(
            db, "ing_001", status=IngestStatus.PENDING_PROCESSING, created_at=now
        )
        _make_ingest_job(
            db,
            "ing_002",
            status=IngestStatus.CANCELLED,
            created_at=now + timedelta(minutes=1),
        )

        result = find_merge_candidates(db, "900123456", time_window_minutes=5)

        assert len(result) == 0

    def test_find_merge_candidates_respects_time_window(self, db: Session):
        """Jobs 10 min apart with 5-min window -> not grouped."""
        from app.services.ingest_matcher import find_merge_candidates

        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
        _make_ingest_job(db, "ing_001", created_at=now)
        _make_ingest_job(db, "ing_002", created_at=now + timedelta(minutes=10))

        result = find_merge_candidates(db, "900123456", time_window_minutes=5)

        assert len(result) == 0

    def test_find_merge_candidates_single_job_returns_empty(self, db: Session):
        """Only 1 job -> no suggestions."""
        from app.services.ingest_matcher import find_merge_candidates

        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
        _make_ingest_job(db, "ing_001", created_at=now)

        result = find_merge_candidates(db, "900123456", time_window_minutes=5)

        assert len(result) == 0
