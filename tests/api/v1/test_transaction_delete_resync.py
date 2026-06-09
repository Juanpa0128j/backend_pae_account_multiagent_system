"""Tests for derived-statement resync after transaction deletes.

Uses SQLite in-memory (no external DB) following the pattern in
tests/account_process/test_persist_orchestrator.py.

Vía A derivation is manual, so a delete refreshes IN PLACE only the periods that
already have derived statements (preserving their bounds + frequency), instead of
auto-deriving a fresh calendar-month span.
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
P_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
P_END = datetime(2026, 12, 31, tzinfo=timezone.utc)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def _add_statement(
    db,
    *,
    stmt_id: str,
    stmt_type: str,
    source_mode: str,
    frequency: str | None = None,
    period_start: datetime = P_START,
    period_end: datetime = P_END,
):
    stmt = FinancialStatement(
        id=stmt_id,
        ingest_id="ingest-1",
        statement_type=stmt_type,
        period_start=period_start,
        period_end=period_end,
        entity_nit=COMPANY_NIT,
        source_mode=source_mode,
        frequency=frequency,
        data={"totales": {}},
    )
    db.add(stmt)
    db.commit()
    return stmt


def _add_journal_line(db):
    line = JournalEntryLine(
        transaction_posted_id="posted-1",
        fecha=datetime(2026, 6, 15, tzinfo=timezone.utc),
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
        _add_statement(
            db_session,
            stmt_id="bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
        )
        _add_statement(
            db_session,
            stmt_id="er",
            stmt_type="estado_resultados",
            source_mode="derived_from_journal",
        )
        # A NIC 7 secondary derived from the journal — must also be purged.
        _add_statement(
            db_session,
            stmt_id="sec-flujo",
            stmt_type="flujo_de_caja",
            source_mode="derived",
        )
        _add_statement(
            db_session,
            stmt_id="direct-bg",
            stmt_type="balance_general",
            source_mode="direct",
        )
        # No JournalEntryLine rows for the company.

        _resync_derived_statements(db_session, COMPANY_NIT)

        ids = {s.id for s in db_session.query(FinancialStatement).all()}
        # Journal-derived first-level + secondary purged; the direct (Vía B) survives.
        assert ids == {"direct-bg"}

    def test_journal_remaining_refreshes_in_place(self, db_session, monkeypatch):
        # A stale annual derived BG + a remaining journal line → refresh in place.
        _add_statement(
            db_session,
            stmt_id="stale-bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
            frequency="annual",
        )
        _add_journal_line(db_session)

        calls = {}

        def fake_build(db, *, company_nit, period_start, period_end, frequency=None):
            calls["build"] = {
                "company_nit": company_nit,
                "period_start": period_start,
                "period_end": period_end,
                "frequency": frequency,
            }
            return {"status": "built", "created": {}}

        monkeypatch.setattr(
            "app.services.financial_statement_service.build_first_level_from_journal_entries",
            fake_build,
        )

        _resync_derived_statements(db_session, COMPANY_NIT)

        # Rebuilt the existing period, preserving bounds + frequency. (SQLite
        # drops tzinfo on round-trip, so compare by date — Postgres keeps it.)
        assert calls["build"]["company_nit"] == COMPANY_NIT
        assert calls["build"]["period_start"].date() == P_START.date()
        assert calls["build"]["period_end"].date() == P_END.date()
        assert calls["build"]["frequency"] == "annual"
        # The stale row was deleted before rebuild (fake_build doesn't re-insert).
        assert (
            db_session.query(FinancialStatement)
            .filter(FinancialStatement.id == "stale-bg")
            .first()
            is None
        )

    def test_journal_remaining_re_derives_secondary_when_present(
        self, db_session, monkeypatch
    ):
        # Period has both first-level + secondary → secondary is re-derived too.
        _add_statement(
            db_session,
            stmt_id="fl-bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
            frequency="annual",
        )
        _add_statement(
            db_session,
            stmt_id="sec-flujo",
            stmt_type="flujo_de_caja",
            source_mode="derived",
            frequency="annual",
        )
        _add_journal_line(db_session)

        monkeypatch.setattr(
            "app.services.financial_statement_service.build_first_level_from_journal_entries",
            lambda *a, **k: {"status": "built", "created": {}},
        )
        derive_calls = {}

        def fake_derive(*, company_nit, period_start, period_end, **kwargs):
            derive_calls["company_nit"] = company_nit
            return {"status": "derived"}

        monkeypatch.setattr(
            "app.services.financial_statement_service.derive_financial_statements",
            fake_derive,
        )

        _resync_derived_statements(db_session, COMPANY_NIT)

        assert derive_calls.get("company_nit") == COMPANY_NIT

    def test_none_nit_is_noop(self, db_session):
        _add_statement(
            db_session,
            stmt_id="bg",
            stmt_type="balance_general",
            source_mode="derived_from_journal",
        )
        _resync_derived_statements(db_session, None)
        assert db_session.query(FinancialStatement).count() == 1
