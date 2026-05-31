"""Unit tests for db_service NationalRate helpers.

Uses MagicMock pattern (cf. test_db_service_reteica_tarifa.py).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.database import NationalRate
from app.services import db_service


@pytest.fixture
def db():
    return MagicMock(spec=Session)


def _make_rate(
    code: str = "retefuente_servicios",
    value: float = 0.04,
    descripcion: str = "Ret. fuente servicios",
    norma_referencia: str = "Art. 392 ET",
    vigente_desde: date = date(2023, 1, 1),
) -> MagicMock:
    row = MagicMock(spec=NationalRate)
    row.code = code
    row.value = Decimal(str(value))
    row.descripcion = descripcion
    row.norma_referencia = norma_referencia
    row.vigente_desde = vigente_desde
    return row


class TestListNationalRates:
    def test_returns_all_rows(self, db):
        row = _make_rate()
        db.query.return_value.order_by.return_value.all.return_value = [row]
        result = db_service.list_national_rates(db)
        assert len(result) == 1
        assert result[0]["code"] == "retefuente_servicios"
        assert result[0]["value"] == pytest.approx(0.04)

    def test_returns_empty_list_when_no_rows(self, db):
        db.query.return_value.order_by.return_value.all.return_value = []
        result = db_service.list_national_rates(db)
        assert result == []

    def test_value_serialized_as_float_not_decimal(self, db):
        row = _make_rate(value=0.025)
        db.query.return_value.order_by.return_value.all.return_value = [row]
        result = db_service.list_national_rates(db)
        assert isinstance(result[0]["value"], float)


class TestGetNationalRate:
    def test_returns_row_for_known_code(self, db):
        row = _make_rate()
        db.query.return_value.filter.return_value.first.return_value = row
        result = db_service.get_national_rate(db, "retefuente_servicios")
        assert result is row

    def test_returns_none_for_unknown_code(self, db):
        db.query.return_value.filter.return_value.first.return_value = None
        result = db_service.get_national_rate(db, "nonexistent")
        assert result is None


class TestUpsertNationalRate:
    def test_inserts_new_row(self, db):
        db.query.return_value.filter.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None
        db_service.upsert_national_rate(
            db,
            code="renta_general",
            value=Decimal("0.35"),
            descripcion="Tarifa general renta",
            norma_referencia="Art. 240 ET",
            vigente_desde=date(2023, 1, 1),
        )
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_row(self, db):
        existing = _make_rate(code="renta_general", value=0.33)
        db.query.return_value.filter.return_value.first.return_value = existing
        db.refresh.side_effect = lambda r: None
        db_service.upsert_national_rate(
            db,
            code="renta_general",
            value=Decimal("0.35"),
            descripcion="Tarifa general renta",
            norma_referencia="Art. 240 ET, L.2277/2022",
            vigente_desde=date(2023, 1, 1),
        )
        assert existing.value == Decimal("0.35")
        db.add.assert_not_called()
        db.commit.assert_called_once()
