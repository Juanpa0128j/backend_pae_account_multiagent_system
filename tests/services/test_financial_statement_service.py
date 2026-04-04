import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


def test_build_first_level_skips_existing_types():
    """If all statement types already exist for company+period, none are re-created."""
    from app.services import financial_statement_service as fss
    db = MagicMock()

    with patch.object(fss, "_first_level_type_exists", return_value=True), \
         patch.object(fss.db_service, "create_financial_statement") as mock_create:
        result = fss.build_first_level_from_journal_entries(
            db,
            company_nit="800999888",
            period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    mock_create.assert_not_called()
    assert result["skipped"] == 4


def test_build_first_level_creates_when_missing():
    """When no statements exist, all 4 types should be created."""
    from app.services import financial_statement_service as fss
    db = MagicMock()

    mock_stmt = MagicMock()
    mock_stmt.id = "stmt-id-1"
    mock_ingest = MagicMock()
    mock_ingest.id = "ingest-id-1"

    with patch.object(fss, "_first_level_type_exists", return_value=False), \
         patch.object(fss, "_create_derivation_ingest_job", return_value=mock_ingest), \
         patch.object(fss.db_service, "get_balance_sheet", return_value={"total_activos": 100}), \
         patch.object(fss.db_service, "get_pnl", return_value={"utilidad_neta": 50}), \
         patch.object(fss.db_service, "get_general_ledger", return_value=[]), \
         patch.object(fss.db_service, "get_journal_entry_lines", return_value=[]), \
         patch.object(fss.db_service, "create_financial_statement", return_value=mock_stmt) as mock_create:
        result = fss.build_first_level_from_journal_entries(
            db,
            company_nit="800999888",
            period_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    assert mock_create.call_count == 4
    assert result["skipped"] == 0
    assert len(result["created"]) == 4
