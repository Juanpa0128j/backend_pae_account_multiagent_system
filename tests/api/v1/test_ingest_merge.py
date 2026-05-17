"""Tests for ingest merge endpoints."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.database import IngestJob, IngestStatus, TransactionPending
from main import app


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
def client(db_engine):
    """TestClient with shared in-memory SQLite."""
    SessionLocal = sessionmaker(bind=db_engine)

    def get_db_test():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_db_test
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def db(db_engine):
    """Direct DB session for seeding."""
    SessionLocal = sessionmaker(bind=db_engine)
    session = SessionLocal()
    yield session
    session.close()


def _make_job(
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


def _make_transaction(
    db: Session, txn_id: str, ingest_id: str, raw_data: dict | None = None
) -> TransactionPending:
    txn = TransactionPending(
        id=txn_id,
        ingest_id=ingest_id,
        raw_data=raw_data,
    )
    db.add(txn)
    db.commit()
    return txn


class TestMergeSuggestions:
    def test_merge_suggestions_endpoint_returns_candidates(
        self, client: TestClient, db: Session
    ):
        """GET /merge-suggestions returns grouped jobs."""
        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
        _make_job(db, "ing_001", created_at=now)
        _make_job(db, "ing_002", created_at=now + timedelta(minutes=2))

        response = client.get("/api/v1/ingest/merge-suggestions?company_nit=900123456")
        assert response.status_code == 200
        data = response.json()
        assert "suggestions" in data
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["ingest_ids"] == ["ing_001", "ing_002"]

    def test_merge_suggestions_endpoint_requires_company_nit(self, client: TestClient):
        """GET /merge-suggestions without company_nit returns 422."""
        response = client.get("/api/v1/ingest/merge-suggestions")
        assert response.status_code == 422


class TestMergeEndpoint:
    def test_merge_endpoint_combines_raw_data(self, client: TestClient, db: Session):
        """PATCH /{ingest_id}/merge combines raw_data and cancels source."""
        _make_job(db, "ing_target", status=IngestStatus.COMPLETED)
        _make_job(db, "ing_source", status=IngestStatus.COMPLETED)
        _make_transaction(db, "txn_target", "ing_target", raw_data={"page": 1})
        _make_transaction(db, "txn_source", "ing_source", raw_data={"page": 2})

        response = client.patch(
            "/api/v1/ingest/ing_target/merge",
            json={"source_ingest_id": "ing_source"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ingest_id"] == "ing_target"

        # Verify source is cancelled
        db.expire_all()
        source = db.query(IngestJob).filter(IngestJob.id == "ing_source").first()
        assert source.status == IngestStatus.CANCELLED
        assert any(
            "Merged into ing_target" in e for e in (source.extraction_errors or [])
        )

        # Verify target raw_data merged
        target_txn = (
            db.query(TransactionPending)
            .filter(TransactionPending.ingest_id == "ing_target")
            .first()
        )
        assert isinstance(target_txn.raw_data, list)
        assert target_txn.raw_data == [{"page": 1}, {"page": 2}]

    def test_merge_endpoint_rejects_different_companies(
        self, client: TestClient, db: Session
    ):
        """PATCH returns 400 when jobs belong to different companies."""
        _make_job(db, "ing_target", company_nit="900111111")
        _make_job(db, "ing_source", company_nit="900222222")

        response = client.patch(
            "/api/v1/ingest/ing_target/merge",
            json={"source_ingest_id": "ing_source"},
        )
        assert response.status_code == 400

    def test_merge_endpoint_rejects_cancelled_source(
        self, client: TestClient, db: Session
    ):
        """PATCH returns 400 when source is already cancelled."""
        _make_job(db, "ing_target")
        _make_job(db, "ing_source", status=IngestStatus.CANCELLED)

        response = client.patch(
            "/api/v1/ingest/ing_target/merge",
            json={"source_ingest_id": "ing_source"},
        )
        assert response.status_code == 400
