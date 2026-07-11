"""Feature tests for shared ingest file storage (ingest_files table)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base, IngestFile, IngestJob, IngestStatus


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_job(db, ingest_id="ing_test_1", file_name="factura.pdf"):
    job = IngestJob(
        id=ingest_id,
        file_name=file_name,
        file_path=None,
        status=IngestStatus.PENDING_REVIEW,
    )
    db.add(job)
    db.commit()
    return job


class TestIngestFileModel:
    def test_roundtrip_bytes(self, db):
        _make_job(db)
        row = IngestFile(
            id="ingf_1",
            ingest_id="ing_test_1",
            file_name="factura.pdf",
            content=b"%PDF-1.4 fake bytes",
        )
        db.add(row)
        db.commit()

        loaded = db.query(IngestFile).filter_by(ingest_id="ing_test_1").one()
        assert loaded.content == b"%PDF-1.4 fake bytes"
        assert loaded.file_name == "factura.pdf"
        assert loaded.created_at is not None
