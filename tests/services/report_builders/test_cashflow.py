from unittest.mock import MagicMock

from app.services.report_builders.cashflow import build_cashflow


def test_build_cashflow_returns_dict():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    result = build_cashflow(MagicMock(), {}, svc)
    assert isinstance(result, dict)


def test_build_cashflow_calls_get_general_ledger():
    svc = MagicMock()
    svc.get_general_ledger.return_value = []
    build_cashflow(MagicMock(), {}, svc)
    svc.get_general_ledger.assert_called()
