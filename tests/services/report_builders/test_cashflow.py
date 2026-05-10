"""Tests for the cash flow report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.cashflow import build_cashflow

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"


def test_empty_ledger_returns_zeros():
    """No journal entries → total efectivo zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    result = build_cashflow(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_efectivo"] == 0.0
    assert result["cuentas_efectivo"] == []
    assert result["report_type"] == "cash_flow"


def test_single_account_type_only():
    """Only one class-11 account present."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "1110",
            "name": "Bancos",
            "total_debit": 5_000_000.0,
            "total_credit": 1_000_000.0,
            "net_balance": 4_000_000.0,
        }
    ]

    result = build_cashflow(None, {"start_date": _START, "end_date": _END}, svc)

    assert len(result["cuentas_efectivo"]) == 1
    assert result["cuentas_efectivo"][0]["codigo"] == "1110"
    assert result["total_efectivo"] == 4_000_000.0


def test_mixed_entries():
    """Multiple class-11 accounts → summed correctly."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "1110",
            "name": "Bancos",
            "total_debit": 5_000_000.0,
            "total_credit": 1_000_000.0,
            "net_balance": 4_000_000.0,
        },
        {
            "account": "1105",
            "name": "Caja",
            "total_debit": 500_000.0,
            "total_credit": 200_000.0,
            "net_balance": 300_000.0,
        },
        {
            "account": "2105",
            "name": "Obligaciones",
            "total_debit": 0.0,
            "total_credit": 2_000_000.0,
            "net_balance": -2_000_000.0,
        },
    ]

    result = build_cashflow(None, {"start_date": _START, "end_date": _END}, svc)

    assert len(result["cuentas_efectivo"]) == 2
    assert result["total_efectivo"] == 4_300_000.0


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_cashflow(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_general_ledger.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_cashflow(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_general_ledger.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
