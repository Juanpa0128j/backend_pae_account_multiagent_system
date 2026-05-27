"""Unit tests for PersistOrchestrator using SQLite in-memory.

No external DB required — tables are created from SQLAlchemy Base metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import FinancialStatement, JournalEntryLine
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


# ------------------------------------------------------------------
# derive_and_persist_statements
# ------------------------------------------------------------------


class TestDeriveAndPersistStatements:
    def test_derives_balance_general_and_estado_resultados(
        self, orchestrator, db_session
    ):
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Efectivo",
                "debito": "2000000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "220505",
                "descripcion": "Proveedores",
                "tercero_nit": "900123456",
                "detalle": "CxP",
                "debito": "0",
                "credito": "500000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "311505",
                "descripcion": "Capital",
                "tercero_nit": "900123456",
                "detalle": "Aporte",
                "debito": "0",
                "credito": "1000000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "415505",
                "descripcion": "Ingresos",
                "tercero_nit": "900123456",
                "detalle": "Venta",
                "debito": "0",
                "credito": "1000000",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "513505",
                "descripcion": "Gastos",
                "tercero_nit": "900123456",
                "detalle": "Servicios",
                "debito": "200000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "613505",
                "descripcion": "Costos",
                "tercero_nit": "900123456",
                "detalle": "Mercancia",
                "debito": "300000",
                "credito": "0",
            },
        ]

        period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2026, 3, 31, tzinfo=timezone.utc)

        result = orchestrator.derive_and_persist_statements(
            entries,
            ingest_id="ing_001",
            company_nit="900123456",
            period_start=period_start,
            period_end=period_end,
        )

        assert "balance_general" in result
        assert "estado_resultados" in result

        bg = result["balance_general"]
        assert bg.statement_type == "balance_general"
        assert bg.entity_nit == "900123456"
        assert bg.data["total_activos"] == 2000000.0
        assert bg.data["total_pasivos"] == 500000.0
        assert bg.data["patrimonio_sin_utilidad"] == 1000000.0
        assert bg.data["utilidad_neta"] == 500000.0
        assert bg.data["total_patrimonio"] == 1500000.0
        assert bg.data["cuadre"] is True

        er = result["estado_resultados"]
        assert er.statement_type == "estado_resultados"
        assert er.data["utilidad_bruta"] == 700000.0
        assert er.data["utilidad_neta"] == 500000.0

    def test_commits_statements_to_db(self, orchestrator, db_session):
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Deposito",
                "debito": "500000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "220505",
                "descripcion": "Proveedores",
                "tercero_nit": "900123456",
                "detalle": "CxP",
                "debito": "0",
                "credito": "500000",
            },
        ]

        period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2026, 3, 31, tzinfo=timezone.utc)

        orchestrator.derive_and_persist_statements(
            entries,
            ingest_id="ing_002",
            company_nit="900123456",
            period_start=period_start,
            period_end=period_end,
        )

        count = (
            db_session.query(FinancialStatement).filter_by(ingest_id="ing_002").count()
        )
        assert count == 2

    def test_redrive_replaces_prior_journal_derived_rows(
        self, orchestrator, db_session
    ):
        """Re-deriving the same period must not duplicate BG/ER rows.

        Each processed document re-derives the whole period from the
        cumulative journal; the latest derivation replaces the prior one.
        """
        entries = [
            {
                "fecha": "2026-03-15",
                "cuenta": "110505",
                "descripcion": "Caja",
                "tercero_nit": "900123456",
                "detalle": "Efectivo",
                "debito": "1000000",
                "credito": "0",
            },
            {
                "fecha": "2026-03-15",
                "cuenta": "311505",
                "descripcion": "Capital",
                "tercero_nit": "900123456",
                "detalle": "Aporte",
                "debito": "0",
                "credito": "1000000",
            },
        ]
        period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2026, 3, 31, tzinfo=timezone.utc)

        # First document
        orchestrator.derive_and_persist_statements(
            entries,
            ingest_id="ing_a",
            company_nit="900123456",
            period_start=period_start,
            period_end=period_end,
        )
        # Second document — re-derives the same period
        orchestrator.derive_and_persist_statements(
            entries,
            ingest_id="ing_b",
            company_nit="900123456",
            period_start=period_start,
            period_end=period_end,
        )

        bg_count = (
            db_session.query(FinancialStatement)
            .filter_by(
                entity_nit="900123456",
                statement_type="balance_general",
                source_mode="derived_from_journal",
            )
            .count()
        )
        er_count = (
            db_session.query(FinancialStatement)
            .filter_by(
                entity_nit="900123456",
                statement_type="estado_resultados",
                source_mode="derived_from_journal",
            )
            .count()
        )
        assert bg_count == 1
        assert er_count == 1

    def test_empty_entries_creates_zero_statements(self, orchestrator, db_session):
        period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2026, 3, 31, tzinfo=timezone.utc)

        result = orchestrator.derive_and_persist_statements(
            [],
            ingest_id="ing_003",
            company_nit="900123456",
            period_start=period_start,
            period_end=period_end,
        )

        bg = result["balance_general"]
        assert bg.data["total_activos"] == 0.0
        assert bg.data["cuadre"] is True

        er = result["estado_resultados"]
        assert er.data["utilidad_neta"] == 0.0
