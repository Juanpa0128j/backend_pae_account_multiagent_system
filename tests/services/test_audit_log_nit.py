"""Verify get_recent_activity scopes audit log entries by company_nit."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import AuditLog
from app.services import db_service


NIT_A = "900111111"
NIT_B = "900222222"


@pytest.fixture()
def db():
    """In-memory SQLite session with audit logs across 2 NITs + NULLs."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    rows = [
        AuditLog(
            action="transaction_pending_created",
            entity_id="txn_A_1",
            entity_type="transaction",
            company_nit=NIT_A,
        ),
        AuditLog(
            action="transaction_posted",
            entity_id="posted_A_1",
            entity_type="transaction",
            company_nit=NIT_A,
        ),
        AuditLog(
            action="transaction_pending_created",
            entity_id="txn_B_1",
            entity_type="transaction",
            company_nit=NIT_B,
        ),
        # Pre-tenant / process events — NULL nit must NOT leak under filter
        AuditLog(
            action="process_created",
            entity_id="proc_x",
            entity_type="process",
            company_nit=None,
        ),
        AuditLog(
            action="ingest_created",
            entity_id="ing_legacy",
            entity_type="ingest",
            company_nit=None,
        ),
    ]
    session.add_all(rows)
    session.commit()

    yield session

    session.close()


def test_get_recent_activity_filters_by_nit(db):
    rows_a = db_service.get_recent_activity(db, limit=10, company_nit=NIT_A)
    assert {r["entity_id"] for r in rows_a} == {"txn_A_1", "posted_A_1"}

    rows_b = db_service.get_recent_activity(db, limit=10, company_nit=NIT_B)
    assert {r["entity_id"] for r in rows_b} == {"txn_B_1"}


def test_get_recent_activity_excludes_null_nit_when_filtering(db):
    rows_a = db_service.get_recent_activity(db, limit=10, company_nit=NIT_A)
    entity_types = {r["entity_type"] for r in rows_a}
    assert "process" not in entity_types
    assert "ingest" not in entity_types


def test_get_recent_activity_without_nit_returns_all(db):
    rows = db_service.get_recent_activity(db, limit=10)
    assert len(rows) == 5
