"""Parser mode collapse: enum shrink + stored-row normalization."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.models.database import Base
from app.models.document_types import ParserMode


class TestParserModeEnum:
    def test_exactly_four_canonical_modes(self):
        assert {m.value for m in ParserMode} == {
            "fast",
            "standard",
            "agentic",
            "agentic_plus",
        }

    def test_legacy_values_rejected(self):
        with pytest.raises(ValueError):
            ParserMode("premium")
        with pytest.raises(ValueError):
            ParserMode("gpt4o")


class TestParserModeDataMigration:
    def _db(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        return engine

    def test_update_normalizes_only_legacy_rows(self):
        engine = self._db()
        with engine.begin() as conn:
            for jid, mode in [
                ("ing_1", "premium"),
                ("ing_2", "gpt4o"),
                ("ing_3", "fast"),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO ingest_jobs (id, file_name, status, classification_confirmed, parser_mode)"
                        " VALUES (:id, 'f.pdf', 'PENDING_REVIEW', 0, :mode)"
                    ),
                    {"id": jid, "mode": mode},
                )
            # The exact statement the migration runs:
            conn.execute(
                text(
                    "UPDATE ingest_jobs SET parser_mode = 'agentic'"
                    " WHERE parser_mode IN ('premium', 'gpt4o')"
                )
            )
        with engine.connect() as conn:
            rows = dict(
                conn.execute(text("SELECT id, parser_mode FROM ingest_jobs")).fetchall()
            )
        assert rows == {"ing_1": "agentic", "ing_2": "agentic", "ing_3": "fast"}
