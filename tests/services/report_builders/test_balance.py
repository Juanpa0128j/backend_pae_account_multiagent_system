"""Tests for the balance sheet report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.balance import build_balance

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"

_BALANCE_DATA = {
    "assets": 17_000_000.0,
    "liabilities": 5_000_000.0,
    "equity": 10_000_000.0,
    "revenue": 8_000_000.0,
    "expenses": 2_000_000.0,
    "cost_of_sales": 4_000_000.0,
    "net_profit": 2_000_000.0,
    "total_equity": 12_000_000.0,
    "is_balanced": True,
}


def test_empty_ledger_returns_zeros():
    """No journal entries → balance uses service totals, detail lists empty."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = {
        "assets": 0.0,
        "liabilities": 0.0,
        "equity": 0.0,
        "revenue": 0.0,
        "expenses": 0.0,
        "cost_of_sales": 0.0,
        "net_profit": 0.0,
        "total_equity": 0.0,
        "is_balanced": True,
    }
    svc.get_general_ledger.return_value = []

    result = build_balance(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["activos"] == 0.0
    assert result["pasivos"] == 0.0
    assert result["patrimonio"] == 0.0
    assert result["activos_detalle"] == []
    assert result["pasivos_detalle"] == []
    assert result["patrimonio_detalle"] == []
    assert result["cuadre"] is True
    assert result["report_type"] == "balance_sheet"


def test_single_account_type_only():
    """Only activos in ledger → pasivos and patrimonio detail empty."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = _BALANCE_DATA
    svc.get_general_ledger.return_value = [
        {
            "account": "1110",
            "name": "Bancos",
            "total_debit": 5_000_000.0,
            "total_credit": 1_000_000.0,
            "net_balance": 4_000_000.0,
        }
    ]

    result = build_balance(None, {"start_date": _START, "end_date": _END}, svc)

    assert len(result["activos_detalle"]) == 1
    assert result["activos_detalle"][0]["codigo"] == "1110"
    assert result["pasivos_detalle"] == []
    assert result["patrimonio_detalle"] == []
    assert result["activos"] == 17_000_000.0


def test_mixed_entries():
    """Multiple account types → detail lists populated correctly."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = _BALANCE_DATA
    svc.get_general_ledger.return_value = [
        {
            "account": "1110",
            "name": "Bancos",
            "total_debit": 5_000_000.0,
            "total_credit": 1_000_000.0,
            "net_balance": 4_000_000.0,
        },
        {
            "account": "2105",
            "name": "Obligaciones",
            "total_debit": 0.0,
            "total_credit": 2_000_000.0,
            "net_balance": -2_000_000.0,
        },
        {
            "account": "3110",
            "name": "Capital",
            "total_debit": 0.0,
            "total_credit": 10_000_000.0,
            "net_balance": -10_000_000.0,
        },
    ]

    result = build_balance(None, {"start_date": _START, "end_date": _END}, svc)

    assert len(result["activos_detalle"]) == 1
    assert len(result["pasivos_detalle"]) == 1
    assert len(result["patrimonio_detalle"]) == 1
    assert result["activos_detalle"][0]["saldo"] == 4_000_000.0
    assert result["pasivos_detalle"][0]["saldo"] == 2_000_000.0
    assert result["patrimonio_detalle"][0]["saldo"] == 10_000_000.0


def test_period_filtering():
    """end_date is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = _BALANCE_DATA
    svc.get_general_ledger.return_value = []

    build_balance(None, {"start_date": _START, "end_date": _END}, svc)

    bs_call = svc.get_balance_sheet.call_args
    assert bs_call.kwargs["cutoff_date"].isoformat().startswith(_END)

    gl_call = svc.get_general_ledger.call_args
    assert gl_call.kwargs["end_date"].isoformat().startswith(_END)
    assert gl_call.kwargs["start_date"] is None


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = _BALANCE_DATA
    svc.get_general_ledger.return_value = []

    build_balance(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    assert svc.get_balance_sheet.call_args.kwargs["company_nit"] == _COMPANY_NIT
    assert svc.get_general_ledger.call_args.kwargs["company_nit"] == _COMPANY_NIT
