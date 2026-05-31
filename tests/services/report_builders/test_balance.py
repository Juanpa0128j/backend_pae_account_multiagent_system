from unittest.mock import MagicMock

from app.services.report_builders.balance import build_balance


def _make_svc(balance_data=None, ledger=None):
    svc = MagicMock()
    svc.get_balance_sheet.return_value = balance_data or {
        "activos": [],
        "pasivos": [],
        "patrimonio": [],
        "assets": 0,
        "liabilities": 0,
        "equity": 0,
        "net_profit": 0,
        "total_equity": 0,
        "is_balanced": True,
    }
    svc.get_general_ledger.return_value = ledger or []
    return svc


def test_build_balance_returns_dict():
    result = build_balance(MagicMock(), {}, _make_svc())
    assert isinstance(result, dict)


def test_build_balance_has_report_type():
    result = build_balance(MagicMock(), {}, _make_svc())
    assert result.get("report_type") == "balance_sheet"


def test_build_balance_calls_get_balance_sheet():
    svc = _make_svc()
    build_balance(MagicMock(), {}, svc)
    svc.get_balance_sheet.assert_called_once()


def test_build_balance_passes_company_nit():
    svc = _make_svc()
    build_balance(MagicMock(), {"company_nit": "800999888"}, svc)
    call_kwargs = svc.get_balance_sheet.call_args
    assert "800999888" in str(call_kwargs)
