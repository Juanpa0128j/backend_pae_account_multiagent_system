"""Unit tests for db_service.get_effective_rate — temporal legislative windows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.database import CompanyRateOverride, NationalRate
from app.services import db_service


@pytest.fixture
def db():
    return MagicMock(spec=Session)


def _make_override(
    value: float = 0.035,
    vigente_desde: date = date(2026, 5, 8),
    vigente_hasta: date | None = None,
) -> MagicMock:
    row = MagicMock(spec=CompanyRateOverride)
    row.company_nit = "800999888"
    row.rate_code = "retefuente_servicios"
    row.value = Decimal(str(value))
    row.norma_referencia = "Acuerdo especial"
    row.vigente_desde = vigente_desde
    row.vigente_hasta = vigente_hasta
    return row


def _make_national(
    value: float = 0.04,
    vigente_desde: date = date(2026, 1, 1),
    vigente_hasta: date | None = None,
) -> MagicMock:
    row = MagicMock(spec=NationalRate)
    row.code = "retefuente_servicios"
    row.value = Decimal(str(value))
    row.descripcion = "Retención fuente servicios"
    row.norma_referencia = "Art. 392 ET"
    row.vigente_desde = vigente_desde
    row.vigente_hasta = vigente_hasta
    return row


def _setup_db_mocks(db, override_row, national_row):
    """Wire db.query(...).filter_by(...).first() for override and national."""
    override_query = MagicMock()
    override_query.filter_by.return_value.first.return_value = override_row

    national_query = MagicMock()
    national_query.filter.return_value.first.return_value = national_row

    def _query_side_effect(model):
        if model is CompanyRateOverride:
            return override_query
        if model is NationalRate:
            return national_query
        return MagicMock()

    db.query.side_effect = _query_side_effect


class TestGetEffectiveRate:
    def test_returns_company_override_within_window(self, db):
        override = _make_override(
            value=0.05, vigente_desde=date(2026, 5, 8), vigente_hasta=None
        )
        _setup_db_mocks(db, override_row=override, national_row=None)

        result = db_service.get_effective_rate(
            db, "retefuente_servicios", "800999888", date(2026, 6, 1)
        )

        assert result == Decimal("0.05")

    def test_returns_national_rate_when_no_company_override(self, db):
        national = _make_national(
            value=0.04, vigente_desde=date(2026, 1, 1), vigente_hasta=None
        )
        _setup_db_mocks(db, override_row=None, national_row=national)

        result = db_service.get_effective_rate(
            db, "retefuente_servicios", "800999888", date(2026, 6, 1)
        )

        assert result == Decimal("0.04")

    def test_falls_back_to_none_when_no_db_rate(self, db):
        _setup_db_mocks(db, override_row=None, national_row=None)

        result = db_service.get_effective_rate(
            db, "retefuente_servicios", "800999888", date(2026, 6, 1)
        )

        assert result is None

    def test_expired_override_not_returned(self, db):
        # Override expired before as_of_date → fall through to national
        override = _make_override(
            value=0.05, vigente_desde=date(2026, 1, 1), vigente_hasta=date(2026, 5, 7)
        )
        national = _make_national(value=0.04)
        _setup_db_mocks(db, override_row=override, national_row=national)

        result = db_service.get_effective_rate(
            db, "retefuente_servicios", "800999888", date(2026, 6, 1)
        )

        assert result == Decimal("0.04")

    def test_override_not_yet_active_not_returned(self, db):
        # Override starts in the future → fall through to national
        override = _make_override(
            value=0.05, vigente_desde=date(2026, 8, 1), vigente_hasta=None
        )
        national = _make_national(value=0.04)
        _setup_db_mocks(db, override_row=override, national_row=national)

        result = db_service.get_effective_rate(
            db, "retefuente_servicios", "800999888", date(2026, 6, 1)
        )

        assert result == Decimal("0.04")

    def test_none_as_of_date_matches_open_ended_rows(self, db):
        # as_of_date=None: open-ended override (vigente_hasta=None) should match
        override = _make_override(
            value=0.06, vigente_desde=date(2026, 1, 1), vigente_hasta=None
        )
        _setup_db_mocks(db, override_row=override, national_row=None)

        result = db_service.get_effective_rate(
            db, "retefuente_servicios", "800999888", None
        )

        assert result == Decimal("0.06")


class TestEffectiveRateResponseSchema:
    def test_effective_rate_response_has_vigente_hasta_field(self):
        from app.models.schemas import EffectiveRateResponse

        resp = EffectiveRateResponse(
            code="x",
            value=0.04,
            descripcion="d",
            norma_referencia="n",
            vigente_desde="2026-01-01",
            vigente_hasta="2026-12-31",
            overridden=False,
        )
        assert resp.vigente_hasta == "2026-12-31"
