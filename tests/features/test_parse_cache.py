"""Feature tests for the DB-backed LlamaParse result cache."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base, ParseCache


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


class TestParseCacheModel:
    def test_roundtrip(self, db):
        row = ParseCache(
            content_sha256="a" * 64,
            parser_mode="fast",
            raw_text="# parsed markdown",
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()

        loaded = db.get(ParseCache, ("a" * 64, "fast"))
        assert loaded.raw_text == "# parsed markdown"

    def test_composite_key_mode_isolation(self, db):
        db.add(
            ParseCache(
                content_sha256="b" * 64,
                parser_mode="fast",
                raw_text="F",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            ParseCache(
                content_sha256="b" * 64,
                parser_mode="premium",
                raw_text="P",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        assert db.get(ParseCache, ("b" * 64, "fast")).raw_text == "F"
        assert db.get(ParseCache, ("b" * 64, "premium")).raw_text == "P"
