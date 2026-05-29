from unittest.mock import MagicMock

from app.services.report_builders.pnl import build_pnl


def test_build_pnl_returns_dict():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    result = build_pnl(MagicMock(), {}, svc)
    assert isinstance(result, dict)


def test_build_pnl_has_report_type():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    result = build_pnl(MagicMock(), {}, svc)
    assert result.get("report_type") == "profit_and_loss"


def test_build_pnl_calls_get_general_ledger():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    build_pnl(MagicMock(), {}, svc)
    svc.get_general_ledger.assert_called_once()
