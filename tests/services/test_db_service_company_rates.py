"""Unit tests for db_service company rate override helpers.

Uses MagicMock pattern (cf. test_db_service_national_rates.py).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.database import CompanyRateOverride
from app.services import db_service


@pytest.fixture
def db():
    return MagicMock(spec=Session)


@pytest.fixture(autouse=True)
def reset_list_national_rates_mock():
    """Reset the mocked list_national_rates after each test."""
    original = db_service.list_national_rates
    yield
    db_service.list_national_rates = original


def _make_override(
    company_nit: str = "800999888",
    rate_code: str = "retefuente_servicios",
    value: float = 0.035,
    norma_referencia: str | None = "Acuerdo especial",
    vigente_desde: date = date(2026, 1, 1),
) -> MagicMock:
    row = MagicMock(spec=CompanyRateOverride)
    row.company_nit = company_nit
    row.rate_code = rate_code
    row.value = Decimal(str(value))
    row.norma_referencia = norma_referencia
    row.vigente_desde = vigente_desde
    row.updated_at = datetime.utcnow()
    return row


class TestGetEffectiveRates:
    def test_returns_national_rates_when_no_overrides(self, db):
        """No overrides → return national rates with overridden=False."""
        # Mock national_rates list
        db_service.list_national_rates = MagicMock(
            return_value=[
                {
                    "code": "retefuente_servicios",
                    "value": 0.04,
                    "descripcion": "Ret. fuente servicios",
                    "norma_referencia": "Art. 392 ET",
                    "vigente_desde": "2023-01-01",
                },
                {
                    "code": "retefuente_bienes",
                    "value": 0.025,
                    "descripcion": "Ret. fuente bienes",
                    "norma_referencia": "Art. 401 ET",
                    "vigente_desde": "2023-01-01",
                },
            ]
        )

        # No overrides for this company
        db.query.return_value.filter.return_value.all.return_value = []

        result = db_service.get_effective_rates(db, "800999888")

        assert len(result) == 2
        assert result[0]["code"] == "retefuente_servicios"
        assert result[0]["value"] == 0.04
        assert result[0]["overridden"] is False
        assert result[1]["overridden"] is False

    def test_company_override_replaces_national_value(self, db):
        """Override value replaces national value and sets overridden=True."""
        db_service.list_national_rates = MagicMock(
            return_value=[
                {
                    "code": "retefuente_servicios",
                    "value": 0.04,
                    "descripcion": "Ret. fuente servicios",
                    "norma_referencia": "Art. 392 ET",
                    "vigente_desde": "2023-01-01",
                }
            ]
        )

        override = _make_override(
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=0.035,
            norma_referencia="Acuerdo especial",
        )
        db.query.return_value.filter.return_value.all.return_value = [override]

        result = db_service.get_effective_rates(db, "800999888")

        assert len(result) == 1
        assert result[0]["code"] == "retefuente_servicios"
        assert result[0]["value"] == 0.035
        assert result[0]["overridden"] is True
        assert result[0]["norma_referencia"] == "Acuerdo especial"

    def test_missing_company_falls_back_gracefully(self, db):
        """Query for non-existent company returns national rates."""
        db_service.list_national_rates = MagicMock(
            return_value=[
                {
                    "code": "retefuente_servicios",
                    "value": 0.04,
                    "descripcion": "Ret. fuente servicios",
                    "norma_referencia": "Art. 392 ET",
                    "vigente_desde": "2023-01-01",
                }
            ]
        )

        db.query.return_value.filter.return_value.all.return_value = []

        result = db_service.get_effective_rates(db, "nonexistent_nit")

        assert len(result) == 1
        assert result[0]["overridden"] is False


class TestUpsertCompanyRateOverride:
    def test_inserts_new_override(self, db):
        """New override is inserted."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia="Acuerdo especial",
            vigente_desde=date(2026, 1, 1),
        )

        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_override(self, db):
        """Existing override is updated."""
        existing = _make_override(
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=0.04,
            norma_referencia="Old reference",
        )
        db.query.return_value.filter_by.return_value.first.return_value = existing
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia="New reference",
            vigente_desde=date(2026, 1, 1),
        )

        assert existing.value == Decimal("0.035")
        assert existing.norma_referencia == "New reference"
        assert existing.vigente_desde == date(2026, 1, 1)
        db.add.assert_not_called()
        db.commit.assert_called_once()

    def test_respects_commit_flag(self, db):
        """When commit=False, only flush is called."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia="Acuerdo especial",
            vigente_desde=date(2026, 1, 1),
            commit=False,
        )

        db.add.assert_called_once()
        db.commit.assert_not_called()

    def test_null_norma_referencia(self, db):
        """norma_referencia can be None."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia=None,
            vigente_desde=date(2026, 1, 1),
        )

        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_upsert_company_rate_override_persists_vigente_hasta(self, db):
        """vigente_hasta round-trips correctly on insert."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia="Acuerdo especial",
            vigente_desde=date(2026, 1, 1),
            vigente_hasta=date(2026, 12, 31),
        )

        added_row = db.add.call_args[0][0]
        assert added_row.vigente_hasta == date(2026, 12, 31)

    def test_upsert_company_rate_override_vigente_hasta_updates_existing(self, db):
        """vigente_hasta is updated on existing row."""
        existing = _make_override(
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=0.04,
        )
        existing.vigente_hasta = None
        db.query.return_value.filter_by.return_value.first.return_value = existing
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia="Acuerdo especial",
            vigente_desde=date(2026, 1, 1),
            vigente_hasta=date(2026, 12, 31),
        )

        assert existing.vigente_hasta == date(2026, 12, 31)

    def test_upsert_company_rate_override_vigente_hasta_defaults_none(self, db):
        """vigente_hasta defaults to None when not supplied."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.upsert_company_rate_override(
            db,
            company_nit="800999888",
            rate_code="retefuente_servicios",
            value=Decimal("0.035"),
            norma_referencia=None,
            vigente_desde=date(2026, 1, 1),
        )

        added_row = db.add.call_args[0][0]
        assert added_row.vigente_hasta is None
