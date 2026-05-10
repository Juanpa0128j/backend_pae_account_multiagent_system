"""Tests for the P&L report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.pnl import build_pnl

_START = "2026-01-01"
_END = "2026-01-31"
_COMPANY_NIT = "900123456"


def test_empty_ledger_returns_zeros():
    """No journal entries → all P&L values zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    result = build_pnl(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_ingresos"] == 0.0
    assert result["total_costo_ventas"] == 0.0
    assert result["total_gastos"] == 0.0
    assert result["utilidad_bruta"] == 0.0
    assert result["utilidad_neta"] == 0.0
    assert result["report_type"] == "profit_and_loss"


def test_single_account_type_only():
    """Only ingresos present → costo and gastos zero."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "4135",
            "name": "Servicios",
            "total_debit": 0.0,
            "total_credit": 8_000_000.0,
            "net_balance": -8_000_000.0,
        }
    ]

    result = build_pnl(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_ingresos"] == 8_000_000.0
    assert result["total_costo_ventas"] == 0.0
    assert result["total_gastos"] == 0.0
    assert result["utilidad_bruta"] == 8_000_000.0
    assert result["utilidad_neta"] == 8_000_000.0


def test_mixed_entries():
    """Ingresos, gastos, and costo present → net calculated correctly."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = [
        {
            "account": "4135",
            "name": "Servicios",
            "total_debit": 0.0,
            "total_credit": 8_000_000.0,
            "net_balance": -8_000_000.0,
        },
        {
            "account": "5110",
            "name": "Honorarios",
            "total_debit": 2_000_000.0,
            "total_credit": 0.0,
            "net_balance": 2_000_000.0,
        },
        {
            "account": "6135",
            "name": "Costo servicios",
            "total_debit": 4_000_000.0,
            "total_credit": 0.0,
            "net_balance": 4_000_000.0,
        },
    ]

    result = build_pnl(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["total_ingresos"] == 8_000_000.0
    assert result["total_costo_ventas"] == 4_000_000.0
    assert result["total_gastos"] == 2_000_000.0
    assert result["utilidad_bruta"] == 4_000_000.0
    assert result["utilidad_neta"] == 2_000_000.0


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_pnl(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_general_ledger.call_args
    assert call.kwargs["start_date"].isoformat().startswith(_START)
    assert call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_general_ledger.return_value = []

    build_pnl(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_general_ledger.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
