"""Unit tests for UVT and base_minima db_service helpers."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services import db_service


@pytest.fixture()
def db():
    """In-memory SQLite session with all ORM tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestGetUvt:
    def test_returns_none_when_no_row(self, db):
        result = db_service.get_uvt(db, 2026)
        assert result is None

    def test_returns_value_after_upsert(self, db):
        db_service.upsert_uvt(
            db, year=2026, value=Decimal("52374"), decreto="Decreto 0024/2025"
        )
        result = db_service.get_uvt(db, 2026)
        assert result == Decimal("52374")

    def test_returns_none_for_different_year(self, db):
        db_service.upsert_uvt(db, year=2026, value=Decimal("52374"), decreto=None)
        assert db_service.get_uvt(db, 2025) is None


class TestUpsertUvt:
    def test_insert_then_update(self, db):
        row = db_service.upsert_uvt(
            db, year=2025, value=Decimal("49799"), decreto="Decreto 2229/2024"
        )
        assert row.year == 2025
        assert Decimal(str(row.value)) == Decimal("49799")

        updated = db_service.upsert_uvt(
            db, year=2025, value=Decimal("50000"), decreto="Decreto test"
        )
        assert Decimal(str(updated.value)) == Decimal("50000")
        assert updated.decreto == "Decreto test"

    def test_idempotent_same_values(self, db):
        db_service.upsert_uvt(
            db, year=2024, value=Decimal("47065"), decreto="Decreto 1235/2023"
        )
        db_service.upsert_uvt(
            db, year=2024, value=Decimal("47065"), decreto="Decreto 1235/2023"
        )
        result = db_service.get_uvt(db, 2024)
        assert result == Decimal("47065")


class TestGetBaseMinima:
    def test_returns_none_when_no_row(self, db):
        assert db_service.get_base_minima(db, "retefuente_servicios", 2026) is None

    def test_returns_value_after_upsert(self, db):
        db_service.upsert_base_minima(
            db, concepto="retefuente_servicios", uvt_units=Decimal("4"), year=2026
        )
        result = db_service.get_base_minima(db, "retefuente_servicios", 2026)
        assert result == Decimal("4")

    def test_different_concepto_returns_none(self, db):
        db_service.upsert_base_minima(
            db, concepto="retefuente_servicios", uvt_units=Decimal("4"), year=2026
        )
        assert db_service.get_base_minima(db, "reteica", 2026) is None

    def test_different_year_returns_none(self, db):
        db_service.upsert_base_minima(
            db, concepto="reteica", uvt_units=Decimal("4"), year=2026
        )
        assert db_service.get_base_minima(db, "reteica", 2025) is None


class TestUpsertBaseMinima:
    def test_insert_then_update(self, db):
        row = db_service.upsert_base_minima(
            db, concepto="retefuente_bienes", uvt_units=Decimal("27"), year=2026
        )
        assert row.concepto == "retefuente_bienes"
        assert Decimal(str(row.uvt_units)) == Decimal("27")

        updated = db_service.upsert_base_minima(
            db, concepto="retefuente_bienes", uvt_units=Decimal("30"), year=2026
        )
        assert Decimal(str(updated.uvt_units)) == Decimal("30")


class TestListTaxConstants:
    def test_empty_when_no_data(self, db):
        result = db_service.list_tax_constants(db, 2026)
        assert result["uvt"] is None
        assert result["base_minima"] == []

    def test_returns_all_rows(self, db):
        db_service.upsert_uvt(
            db, year=2026, value=Decimal("52374"), decreto="Decreto 0024/2025"
        )
        for concepto, units in [
            ("retefuente_servicios", Decimal("4")),
            ("retefuente_bienes", Decimal("27")),
            ("retefuente_arrendamiento", Decimal("27")),
            ("reteica", Decimal("4")),
        ]:
            db_service.upsert_base_minima(
                db, concepto=concepto, uvt_units=units, year=2026
            )

        result = db_service.list_tax_constants(db, 2026)
        assert result["uvt"]["year"] == 2026
        assert result["uvt"]["value"] == "52374.00"
        assert result["uvt"]["decreto"] == "Decreto 0024/2025"
        assert len(result["base_minima"]) == 4
        conceptos = {r["concepto"] for r in result["base_minima"]}
        assert conceptos == {
            "retefuente_servicios",
            "retefuente_bienes",
            "retefuente_arrendamiento",
            "reteica",
        }
