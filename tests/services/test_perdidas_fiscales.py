"""
Unit tests for pérdidas fiscales acumuladas helpers in db_service.
All DB operations use an in-memory SQLite DB (no external dependencies).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models.database import CompanySettings, PerdidaFiscalAcumulada
from app.services import db_service

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    # Seed a company
    if not session.query(CompanySettings).filter_by(nit="900000001").first():
        session.add(
            CompanySettings(
                nit="900000001",
                nombre="Test SA",
                tasa_renta=Decimal("0.35"),
                tasa_ica=Decimal("0.00690"),
                tasa_iva_general=Decimal("0.19"),
                tasa_reteica=Decimal("0.00414"),
            )
        )
        session.commit()
    yield session
    session.rollback()
    # Clean up pérdidas between tests
    session.query(PerdidaFiscalAcumulada).delete()
    session.commit()
    session.close()


NIT = "900000001"


# ---------------------------------------------------------------------------
# upsert_perdida
# ---------------------------------------------------------------------------


class TestUpsertPerdida:
    def test_insert_new_record(self, db: Session):
        row = db_service.upsert_perdida(db, NIT, 2021, Decimal("1000000"))
        assert row.id is not None
        assert row.monto_perdida == Decimal("1000000")
        assert row.monto_compensado == Decimal("0")
        assert row.monto_pendiente == Decimal("1000000")

    def test_upsert_updates_existing(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2022, Decimal("500000"))
        row = db_service.upsert_perdida(db, NIT, 2022, Decimal("700000"))
        assert row.monto_perdida == Decimal("700000")
        assert row.monto_pendiente == Decimal("700000")

    def test_upsert_with_decreto_and_notas(self, db: Session):
        row = db_service.upsert_perdida(
            db, NIT, 2023, Decimal("200000"), decreto="Art. 147 ET", notas="Revisar"
        )
        assert row.decreto == "Art. 147 ET"
        assert row.notas == "Revisar"


# ---------------------------------------------------------------------------
# get_perdidas_disponibles / sum_perdidas_disponibles
# ---------------------------------------------------------------------------


class TestGetPerdidasDisponibles:
    def test_returns_fifo_order(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2019, Decimal("300000"))
        db_service.upsert_perdida(db, NIT, 2020, Decimal("200000"))
        rows = db_service.get_perdidas_disponibles(db, NIT, 2026)
        years = [r.year for r in rows]
        assert years == sorted(years), "Must be ascending (FIFO)"

    def test_excludes_current_year(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2026, Decimal("100000"))
        rows = db_service.get_perdidas_disponibles(db, NIT, 2026)
        assert all(r.year < 2026 for r in rows)

    def test_excludes_fully_compensated(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2018, Decimal("100000"))
        db_service.register_compensacion(db, NIT, 2018, Decimal("100000"))
        rows = db_service.get_perdidas_disponibles(db, NIT, 2026)
        assert all(r.year != 2018 for r in rows)

    def test_sum_equals_sum_of_pending(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2015, Decimal("400000"))
        db_service.upsert_perdida(db, NIT, 2016, Decimal("600000"))
        total = db_service.sum_perdidas_disponibles(db, NIT, 2026)
        rows = db_service.get_perdidas_disponibles(db, NIT, 2026)
        expected = sum(r.monto_pendiente for r in rows)
        assert total == expected


# ---------------------------------------------------------------------------
# register_compensacion
# ---------------------------------------------------------------------------


class TestRegisterCompensacion:
    def test_increments_compensado(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2017, Decimal("500000"))
        row = db_service.register_compensacion(db, NIT, 2017, Decimal("200000"))
        assert row.monto_compensado == Decimal("200000")
        assert row.monto_pendiente == Decimal("300000")

    def test_second_increment_accumulates(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2014, Decimal("1000000"))
        db_service.register_compensacion(db, NIT, 2014, Decimal("300000"))
        row = db_service.register_compensacion(db, NIT, 2014, Decimal("200000"))
        assert row.monto_compensado == Decimal("500000")

    def test_exceed_raises_value_error(self, db: Session):
        db_service.upsert_perdida(db, NIT, 2013, Decimal("100000"))
        with pytest.raises(ValueError, match="excedería"):
            db_service.register_compensacion(db, NIT, 2013, Decimal("200000"))

    def test_missing_record_raises_value_error(self, db: Session):
        with pytest.raises(ValueError, match="No fiscal loss record"):
            db_service.register_compensacion(db, NIT, 1999, Decimal("1"))
