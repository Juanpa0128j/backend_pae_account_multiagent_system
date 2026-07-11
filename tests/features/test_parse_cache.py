"""Feature tests for the DB-backed LlamaParse result cache."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base, ParseCache
from app.services import parse_cache_service as pcs


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


@pytest.fixture()
def session_factory(db):
    # Returns the SAME session wrapped so .close() is a no-op — the service
    # closes sessions; tests must keep inspecting the shared sqlite state.
    class _Wrapper:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            self._inner.flush()

    return lambda: _Wrapper(db)


class TestParseCacheService:
    def test_miss_returns_none(self, session_factory):
        assert (
            pcs.get_cached_parse("c" * 64, "fast", session_factory=session_factory)
            is None
        )

    def test_store_then_hit(self, session_factory):
        pcs.store_parse("d" * 64, "fast", "# md", session_factory=session_factory)
        assert (
            pcs.get_cached_parse("d" * 64, "fast", session_factory=session_factory)
            == "# md"
        )

    def test_store_duplicate_key_no_error(self, session_factory):
        pcs.store_parse("e" * 64, "fast", "first", session_factory=session_factory)
        pcs.store_parse("e" * 64, "fast", "second", session_factory=session_factory)
        # First write wins (DO NOTHING semantics); no exception raised.
        assert (
            pcs.get_cached_parse("e" * 64, "fast", session_factory=session_factory)
            == "first"
        )

    def test_oversized_text_not_stored(self, session_factory):
        big = "x" * (pcs.MAX_CACHED_TEXT_BYTES + 1)
        pcs.store_parse("f" * 64, "fast", big, session_factory=session_factory)
        assert (
            pcs.get_cached_parse("f" * 64, "fast", session_factory=session_factory)
            is None
        )

    def test_db_error_degrades_to_none(self):
        def broken_factory():
            raise RuntimeError("db down")

        assert (
            pcs.get_cached_parse("a" * 64, "fast", session_factory=broken_factory)
            is None
        )
        pcs.store_parse(
            "a" * 64, "fast", "text", session_factory=broken_factory
        )  # no raise

    def test_sweep_on_store_deletes_expired(self, db, session_factory):
        db.add(
            ParseCache(
                content_sha256="g" * 64,
                parser_mode="fast",
                raw_text="old",
                created_at=datetime.now(timezone.utc) - timedelta(days=8),
            )
        )
        db.commit()
        pcs.store_parse("h" * 64, "fast", "new", session_factory=session_factory)
        assert (
            pcs.get_cached_parse("g" * 64, "fast", session_factory=session_factory)
            is None
        )
        assert (
            pcs.get_cached_parse("h" * 64, "fast", session_factory=session_factory)
            == "new"
        )

    def test_fresh_row_survives_sweep(self, db, session_factory):
        """A non-expired row survives when sweep runs during another store_parse call."""
        # Pre-store row A (created now, so not expired)
        db.add(
            ParseCache(
                content_sha256="i" * 64,
                parser_mode="fast",
                raw_text="fresh_row_text",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        # Call store_parse which triggers a sweep
        pcs.store_parse("j" * 64, "fast", "new_text", session_factory=session_factory)

        # Row A should still exist
        retrieved = pcs.get_cached_parse(
            "i" * 64, "fast", session_factory=session_factory
        )
        assert retrieved == "fresh_row_text"

    def test_store_parse_tolerates_sweep_failure(self):
        """store_parse doesn't raise if the sweep query fails."""

        # Create a session factory that always fails on query()
        def failing_factory():
            class FailingSession:
                def query(self, *args, **kwargs):
                    raise RuntimeError("db failure during sweep")

                def rollback(self):
                    pass

                def close(self):
                    pass

            return FailingSession()

        # Should complete without raising
        pcs.store_parse("k" * 64, "fast", "some_text", session_factory=failing_factory)
