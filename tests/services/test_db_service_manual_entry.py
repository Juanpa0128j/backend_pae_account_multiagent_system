"""DB roundtrip tests for manual transaction helpers.

These tests verify create_manual_ingest_job and update_transaction_pending
using the in-memory SQLite fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import (
    Base,
    IngestJob,
    IngestStatus,
    TransactionPending,
    TransactionStatus,
)
from app.services import db_service
from app.models.document_types import DocumentType, IngestPathway


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_create_manual_ingest_job_creates_synthetic_job(db):
    job = db_service.create_manual_ingest_job(db, company_nit="900.123.456")
    assert job is not None
    assert db.query(IngestJob).filter(IngestJob.id == job.id).first() is not None
    assert job.status == IngestStatus.COMPLETED
    assert job.document_type == DocumentType.MANUAL_ENTRY.value
    assert job.pathway == IngestPathway.BUILD_FROM_SCRATCH.value
    assert job.classification_confirmed is True
    assert job.file_name == "manual_entry"


def test_create_manual_ingest_job_normalizes_nit(db):
    job = db_service.create_manual_ingest_job(db, company_nit="900.123.456")
    assert job.company_nit == "900123456"


def test_update_transaction_pending_updates_fields(db):
    ingest = IngestJob(
        id="ig_manual", file_name="manual.pdf", status=IngestStatus.COMPLETED
    )
    db.add(ingest)
    db.commit()

    txn = TransactionPending(
        id="txn_manual",
        ingest_id="ig_manual",
        company_nit="900",
        fecha=datetime(2024, 1, 1, tzinfo=timezone.utc),
        nit_emisor="800",
        descripcion="original",
        total=Decimal("1000"),
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    db.commit()

    updated = db_service.update_transaction_pending(
        db,
        txn_id="txn_manual",
        descripcion="updated",
        total=Decimal("2000"),
    )
    assert updated is not None
    assert updated.descripcion == "updated"
    assert updated.total == Decimal("2000")
    assert updated.fecha.replace(tzinfo=None) == datetime(2024, 1, 1)
    assert updated.nit_emisor == "800"


def test_update_transaction_pending_returns_none_for_missing(db):
    result = db_service.update_transaction_pending(
        db, txn_id="nonexistent", descripcion="updated"
    )
    assert result is None
