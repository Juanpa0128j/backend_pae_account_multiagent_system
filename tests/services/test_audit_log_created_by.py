"""Verify AuditLog.created_by is populated from current_user.id."""

from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import AuditLog
from app.services import db_service

TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def test_create_ingest_job_sets_created_by(db):
    """create_ingest_job must populate AuditLog.created_by with the user UUID string."""
    db_service.create_ingest_job(
        db,
        file_name="test.pdf",
        created_by=str(TEST_USER_ID),
    )

    log = db.query(AuditLog).filter(AuditLog.action == "ingest_created").first()
    assert log is not None
    assert log.created_by == str(TEST_USER_ID)
