"""Tests for sum_nomina_retefuente db_service function."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from app.services import db_service


class TestSumNominaRetefuente:
    """Tests for sum_nomina_retefuente."""

    def test_returns_zero_when_no_nomina_transactions(self):
        db = MagicMock()
        db.execute.return_value.scalar.return_value = None
        result = db_service.sum_nomina_retefuente(
            db,
            company_nit="800999888",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )
        assert result == 0.0

    def test_returns_sum_from_scalar(self):
        db = MagicMock()
        db.execute.return_value.scalar.return_value = Decimal("1500000.00")
        result = db_service.sum_nomina_retefuente(
            db,
            company_nit="800999888",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        )
        assert result == 1500000.0

    def test_accepts_none_dates(self):
        db = MagicMock()
        db.execute.return_value.scalar.return_value = Decimal("0")
        result = db_service.sum_nomina_retefuente(db, company_nit="800999888")
        assert result == 0.0
        # Query must still execute (no crash with None dates)
        db.execute.assert_called_once()
