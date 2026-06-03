"""Unit tests for PersistOrchestrator using SQLite in-memory.

No external DB required — tables are created from SQLAlchemy Base metadata.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import JournalEntryLine
from app.account_process.persist_orchestrator import PersistOrchestrator

# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def orchestrator(db_session):
    return PersistOrchestrator(db_session)


# ------------------------------------------------------------------
# persist_journal_entries
# ------------------------------------------------------------------


class TestPersistJournalEntries:
    def test_persist_single_entry(self, orchestrator, db_session):
        entries = [
            {
                "fecha": "2026-03-15T10:30:00+00:00",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Deposito",
                "debito": "500000",
                "credito": "0",
            }
        ]
        lines = orchestrator.persist_journal_entries(
            entries,
            transaction_posted_id="txn_001",
            company_nit="900123456",
        )

        assert len(lines) == 1
        assert lines[0].cuenta_puc == "110505"
        assert lines[0].debito == Decimal("500000")
        assert lines[0].credito == Decimal("0")
        assert lines[0].transaction_posted_id == "txn_001"
        assert lines[0].company_nit == "900123456"

    def test_persist_multiple_entries(self, orchestrator, db_session):
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Deposito",
                "debito": "300000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "220505",
                "descripcion": "Proveedores",
                "tercero_nit": "900123456",
                "detalle": "CxP",
                "debito": "0",
                "credito": "300000",
            },
        ]
        lines = orchestrator.persist_journal_entries(
            entries,
            transaction_posted_id="txn_002",
            company_nit="900123456",
        )

        assert len(lines) == 2
        assert lines[0].cuenta_puc == "110505"
        assert lines[1].cuenta_puc == "220505"

    def test_empty_entries_returns_empty_list(self, orchestrator, db_session):
        lines = orchestrator.persist_journal_entries(
            [],
            transaction_posted_id="txn_003",
            company_nit="900123456",
        )
        assert lines == []

    def test_commits_to_db(self, orchestrator, db_session):
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Deposito",
                "debito": "100000",
                "credito": "0",
            }
        ]
        orchestrator.persist_journal_entries(
            entries,
            transaction_posted_id="txn_004",
            company_nit="900123456",
        )

        # Query back from DB
        count = (
            db_session.query(JournalEntryLine)
            .filter_by(transaction_posted_id="txn_004")
            .count()
        )
        assert count == 1
