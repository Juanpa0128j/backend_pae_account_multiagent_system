from unittest.mock import MagicMock
from datetime import datetime, timezone


def test_financial_statements_exist_returns_true_when_present():
    from app.services import db_service

    db = MagicMock()
    # financial_statements_exist uses func.count(distinct(...)) + .scalar():
    # New query has 6 filters: entity_nit, period_start >=, period_start <, period_end >=, period_end <=, statement_type
    db.query.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.scalar.return_value = (
        3
    )
    result = db_service.financial_statements_exist(
        db,
        company_nit="800999888",
        period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        types=["balance_general", "estado_resultados", "libro_auxiliar"],
    )
    assert result is True


def test_financial_statements_exist_returns_false_when_missing():
    from app.services import db_service

    db = MagicMock()
    # financial_statements_exist uses func.count(distinct(...)) + .scalar():
    # New query has 6 filters: entity_nit, period_start >=, period_start <, period_end >=, period_end <=, statement_type
    db.query.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.scalar.return_value = (
        1
    )
    result = db_service.financial_statements_exist(
        db,
        company_nit="800999888",
        period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        types=["balance_general", "estado_resultados", "libro_auxiliar"],
    )
    assert result is False


def test_get_journal_entry_period_returns_none_when_no_entries():
    from app.services import db_service

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    result = db_service.get_journal_entry_period(db, company_nit="800999888")
    assert result is None


def test_get_journal_entry_period_returns_dates_when_entries_exist():
    from app.services import db_service
    from datetime import datetime, timezone

    db = MagicMock()
    min_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    max_dt = datetime(2024, 12, 31, tzinfo=timezone.utc)
    mock_row = MagicMock()
    mock_row.min_fecha = min_dt
    mock_row.max_fecha = max_dt
    db.query.return_value.filter.return_value.first.return_value = mock_row
    result = db_service.get_journal_entry_period(db, company_nit="800999888")
    assert result == (min_dt, max_dt)
