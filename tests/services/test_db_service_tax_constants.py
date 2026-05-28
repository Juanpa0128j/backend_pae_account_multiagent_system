"""Unit tests for UVT and base_minima db_service helpers."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import TaxBaseMinima
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
            db,
            year=2026,
            value=Decimal("52374"),
            referencia_normativa="Decreto 0024/2025",
        )
        result = db_service.get_uvt(db, 2026)
        assert result == Decimal("52374")

    def test_returns_none_for_different_year(self, db):
        db_service.upsert_uvt(
            db, year=2026, value=Decimal("52374"), referencia_normativa=None
        )
        assert db_service.get_uvt(db, 2025) is None


class TestUpsertUvt:
    def test_insert_then_update(self, db):
        row = db_service.upsert_uvt(
            db,
            year=2025,
            value=Decimal("49799"),
            referencia_normativa="Decreto 2229/2024",
        )
        assert row.year == 2025
        assert Decimal(str(row.value)) == Decimal("49799")

        updated = db_service.upsert_uvt(
            db, year=2025, value=Decimal("50000"), referencia_normativa="Decreto test"
        )
        assert Decimal(str(updated.value)) == Decimal("50000")
        assert updated.referencia_normativa == "Decreto test"

    def test_idempotent_same_values(self, db):
        db_service.upsert_uvt(
            db,
            year=2024,
            value=Decimal("47065"),
            referencia_normativa="Decreto 1235/2023",
        )
        db_service.upsert_uvt(
            db,
            year=2024,
            value=Decimal("47065"),
            referencia_normativa="Decreto 1235/2023",
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
            db,
            year=2026,
            value=Decimal("52374"),
            referencia_normativa="Decreto 0024/2025",
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
        assert result["uvt"]["referencia_normativa"] == "Decreto 0024/2025"
        assert len(result["base_minima"]) == 4
        conceptos = {r["concepto"] for r in result["base_minima"]}
        assert conceptos == {
            "retefuente_servicios",
            "retefuente_bienes",
            "retefuente_arrendamiento",
            "reteica",
        }


# ---------------------------------------------------------------------------
# Fix 3 — temporal base mínima via as_of_date
# ---------------------------------------------------------------------------


def _insert_bm(db, concepto, uvt_units, year, eff_from, eff_to):
    """Insert TaxBaseMinima row with effective_from / effective_to."""
    row = TaxBaseMinima(
        concepto=concepto,
        uvt_units=uvt_units,
        year=year,
        effective_from=eff_from,
        effective_to=eff_to,
    )
    db.add(row)
    db.commit()
    return row


class TestGetBaseMinimaAsOfDate:
    """get_base_minima with as_of_date returns the row valid on that date."""

    def test_returns_pre_572_row_for_date_before_decreto(self, db):
        # pre-572 window: effective_from=2024-01-01, effective_to=2025-05-31
        _insert_bm(
            db,
            "retefuente_servicios",
            Decimal("4"),
            2025,
            date(2024, 1, 1),
            date(2025, 5, 31),
        )
        # decreto 572 window: effective_from=2025-06-01, effective_to=2026-05-07
        _insert_bm(
            db,
            "retefuente_servicios",
            Decimal("2"),
            2025,
            date(2025, 6, 1),
            date(2026, 5, 7),
        )

        result = db_service.get_base_minima(
            db, "retefuente_servicios", 2025, as_of_date=date(2025, 4, 15)
        )
        assert result == Decimal("4"), (
            "Should return pre-572 value (4 UVT) before Jun 2025"
        )

    def test_returns_decreto_572_row_during_decreto_window(self, db):
        _insert_bm(
            db,
            "retefuente_servicios",
            Decimal("4"),
            2025,
            date(2024, 1, 1),
            date(2025, 5, 31),
        )
        _insert_bm(
            db,
            "retefuente_servicios",
            Decimal("2"),
            2025,
            date(2025, 6, 1),
            date(2026, 5, 7),
        )

        result = db_service.get_base_minima(
            db, "retefuente_servicios", 2025, as_of_date=date(2025, 8, 1)
        )
        assert result == Decimal("2"), (
            "Should return Decreto 572 value (2 UVT) during window"
        )

    def test_returns_post_suspension_row_after_may_7_2026(self, db):
        _insert_bm(
            db,
            "retefuente_servicios",
            Decimal("2"),
            2026,
            date(2025, 6, 1),
            date(2026, 5, 7),
        )
        _insert_bm(
            db, "retefuente_servicios", Decimal("4"), 2026, date(2026, 5, 8), None
        )  # open-ended post-suspension

        result = db_service.get_base_minima(
            db, "retefuente_servicios", 2026, as_of_date=date(2026, 5, 24)
        )
        assert result == Decimal("4"), (
            "Should return post-suspension value (4 UVT) after May 7 2026"
        )

    def test_returns_none_when_no_valid_row_for_date(self, db):
        # Only has a row valid before 2025-06-01
        _insert_bm(
            db,
            "retefuente_servicios",
            Decimal("4"),
            2025,
            date(2024, 1, 1),
            date(2025, 5, 31),
        )

        result = db_service.get_base_minima(
            db, "retefuente_servicios", 2025, as_of_date=date(2025, 7, 1)
        )
        assert result is None, "No row covers Jul 2025 — should return None"

    def test_fallback_to_year_lookup_when_no_as_of_date(self, db):
        """Without as_of_date, year-based lookup works (backward compat)."""
        db_service.upsert_base_minima(
            db, concepto="retefuente_servicios", uvt_units=Decimal("4"), year=2026
        )
        result = db_service.get_base_minima(db, "retefuente_servicios", 2026)
        assert result == Decimal("4")
