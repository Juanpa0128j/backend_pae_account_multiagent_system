"""Tests for derived-statement resync after transaction deletes.

Uses SQLite in-memory (no external DB) following the pattern in
tests/account_process/test_persist_orchestrator.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import FinancialStatement, JournalEntryLine
from app.api.v1.transactions import _resync_derived_statements

COMPANY_NIT = "900123456"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def _add_derived_statement(db, *, stmt_id: str, stmt_type: str, source_mode: str):
    stmt = FinancialStatement(
        id=stmt_id,
        ingest_id="ingest-1",
        statement_type=stmt_type,
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        entity_nit=COMPANY_NIT,
        source_mode=source_mode,
        data={"totales": {}},
    )
    db.add(stmt)
    db.commit()
    return stmt


def _add_journal_line(db, *, line_id_unused=None):
    line = JournalEntryLine(
        transaction_posted_id="posted-1",
        fecha=datetime(2026, 1, 15, tzinfo=timezone.utc),
        company_nit=COMPANY_NIT,
        cuenta_puc="510515",
        debito=100000,
        credito=0,
    )
    db.add(line)
    db.commit()
    return line


class TestResyncDerivedStatements:
    def test_no_journal_left_removes_derived_statements(self, db_session):
        # Two journal-derived statements + one user-provided (direct) statement.
        _add_derived_statement(
            db_session,
            stmt_id="bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
        )
        _add_derived_statement(
            db_session,
            stmt_id="er",
            stmt_type="estado_resultados",
            source_mode="derived_from_journal",
        )
        _add_derived_statement(
            db_session,
            stmt_id="direct-bg",
            stmt_type="balance_general",
            source_mode="direct",
        )
        # No JournalEntryLine rows for the company.

        _resync_derived_statements(db_session, COMPANY_NIT)

        remaining = db_session.query(FinancialStatement).all()
        ids = {s.id for s in remaining}
        # Journal-derived BG + ER purged; the direct (Vía B) one survives.
        assert ids == {"direct-bg"}

    def test_journal_remaining_re_derives_statements(self, db_session, monkeypatch):
        # A stale derived statement plus a remaining journal line → re-derive.
        _add_derived_statement(
            db_session,
            stmt_id="stale-bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
        )
        _add_journal_line(db_session)

        called = {}

        def fake_auto_derive(db, company_nit, *, ingest_id=""):
            called["company_nit"] = company_nit
            return True

        monkeypatch.setattr(
            "app.agents.persist_node._auto_derive_statements", fake_auto_derive
        )

        _resync_derived_statements(db_session, COMPANY_NIT)

        # Re-derivation path was taken (idempotent replace), not the purge path.
        assert called.get("company_nit") == COMPANY_NIT
        # Purge did NOT run, so the existing derived row is still present.
        assert (
            db_session.query(FinancialStatement)
            .filter(FinancialStatement.id == "stale-bg")
            .first()
            is not None
        )

    def test_none_nit_is_noop(self, db_session):
        _add_derived_statement(
            db_session,
            stmt_id="bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
        )
        _resync_derived_statements(db_session, None)
        assert db_session.query(FinancialStatement).count() == 1
