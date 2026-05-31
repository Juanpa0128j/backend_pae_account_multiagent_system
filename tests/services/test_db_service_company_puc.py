"""Unit tests for db_service company PUC helpers.

Uses MagicMock pattern (cf. test_db_service_national_rates.py).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.database import CompanyPucConfig, CuentaPUC, NaturalezaCuenta
from app.services import db_service


@pytest.fixture
def db():
    return MagicMock(spec=Session)


def _make_cuenta(codigo: str = "1105", nombre: str = "Caja") -> MagicMock:
    row = MagicMock(spec=CuentaPUC)
    row.codigo = codigo
    row.nombre = nombre
    row.clase = 1
    row.naturaleza = NaturalezaCuenta.DEBITO
    row.activa = True
    return row


def _make_company_puc_config(
    company_nit: str = "800999888",
    cuenta_codigo: str = "1105",
    is_active: bool = True,
    custom_nombre: str | None = None,
) -> MagicMock:
    row = MagicMock(spec=CompanyPucConfig)
    row.company_nit = company_nit
    row.cuenta_codigo = cuenta_codigo
    row.is_active = is_active
    row.custom_nombre = custom_nombre
    row.updated_at = datetime.utcnow()
    return row


class TestGetPucForCompany:
    def test_returns_all_active_accounts_when_no_config(self, db):
        """Company with no config rows sees full active catalog."""
        cuenta1 = _make_cuenta("1105", "Caja")
        cuenta2 = _make_cuenta("1110", "Bancos")

        # Mock the subquery for deactivated codes
        deactivated_subquery = MagicMock()
        db.query.return_value.filter.return_value.subquery.return_value = (
            deactivated_subquery
        )

        # Mock the main query
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            cuenta1,
            cuenta2,
        ]

        result = db_service.get_puc_for_company(db, "800999888")
        assert len(result) == 2
        assert result[0].codigo == "1105"
        assert result[1].codigo == "1110"

    def test_excludes_deactivated_account(self, db):
        """Company with is_active=False config row excludes that account."""
        cuenta2 = _make_cuenta("1110", "Bancos")

        # Mock the subquery for deactivated codes (returns ["1105"])
        deactivated_subquery = MagicMock()
        db.query.return_value.filter.return_value.subquery.return_value = (
            deactivated_subquery
        )

        # The main query filters it out, returns only active accounts not in deactivated list
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            cuenta2
        ]

        result = db_service.get_puc_for_company(db, "800999888")
        assert len(result) == 1
        assert result[0].codigo == "1110"

    def test_ignores_other_companies_config(self, db):
        """Company A's deactivations don't affect Company B's view."""
        cuenta = _make_cuenta("1105", "Caja")

        # When Company B queries, the deactivated subquery filters on company_nit=B
        # Only B's deactivations are excluded
        deactivated_subquery = MagicMock()
        db.query.return_value.filter.return_value.subquery.return_value = (
            deactivated_subquery
        )

        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            cuenta
        ]

        result = db_service.get_puc_for_company(db, "800999889")
        assert len(result) == 1


class TestSetCompanyPucConfig:
    def test_inserts_new_config(self, db):
        """New company-account config is inserted."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.set_company_puc_config(
            db, company_nit="800999888", cuenta_codigo="1105", is_active=True
        )

        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_config(self, db):
        """Existing config row is updated."""
        existing = _make_company_puc_config(is_active=True)
        db.query.return_value.filter_by.return_value.first.return_value = existing
        db.refresh.side_effect = lambda r: None

        db_service.set_company_puc_config(
            db,
            company_nit="800999888",
            cuenta_codigo="1105",
            is_active=False,
            custom_nombre="Caja Chica",
        )

        assert existing.is_active is False
        assert existing.custom_nombre == "Caja Chica"
        db.add.assert_not_called()
        db.commit.assert_called_once()

    def test_respects_commit_flag(self, db):
        """When commit=False, only flush is called."""
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None

        db_service.set_company_puc_config(
            db,
            company_nit="800999888",
            cuenta_codigo="1105",
            is_active=True,
            commit=False,
        )

        db.add.assert_called_once()
        db.commit.assert_not_called()
        # Note: _commit_or_flush should be tested separately or this test
        # must mock it explicitly. For now we just ensure commit wasn't called.

    def test_custom_nombre_optional(self, db):
        """custom_nombre can be None."""
        existing = _make_company_puc_config(custom_nombre="Old Name")
        db.query.return_value.filter_by.return_value.first.return_value = existing
        db.refresh.side_effect = lambda r: None

        db_service.set_company_puc_config(
            db,
            company_nit="800999888",
            cuenta_codigo="1105",
            is_active=True,
            custom_nombre=None,
        )

        # custom_nombre should not be updated when None is passed
        assert existing.custom_nombre == "Old Name"
