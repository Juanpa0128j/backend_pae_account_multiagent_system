"""Tests for the financial analysis report builder."""

from unittest.mock import MagicMock, patch


from app.services.report_builders.analysis import build_analysis

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


def _mock_svc_empty() -> MagicMock:
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
    svc.get_top_accounts.return_value = []
    svc.get_top_terceros.return_value = []
    svc.get_monthly_totals_by_class.return_value = {}
    return svc


def _run_analysis(svc, params):
    with patch("app.core.llm_client.get_llm_client") as mock_llm:
        mock_llm.return_value.generate_financial_analysis.return_value = {}
        return build_analysis(None, params, svc)


def test_empty_ledger_returns_zeros():
    """No journal entries → ratios None, summaries zero."""
    svc = _mock_svc_empty()

    result = _run_analysis(svc, {"start_date": _START, "end_date": _END})

    assert result["report_type"] == "financial_analysis"
    assert result["pnl_summary"]["total_ingresos"] == 0.0
    assert result["pnl_summary"]["utilidad_neta"] == 0.0
    assert result["ratios"]["razon_corriente"] is None
    assert result["anomalies"] == []
    assert result["predicciones_numericas"] == []


def test_single_account_type_only():
    """Only ingresos present → net profit equals ingresos."""
    svc = _mock_svc_empty()
    svc.get_general_ledger.return_value = [
        {
            "account": "4135",
            "name": "Servicios",
            "total_debit": 0.0,
            "total_credit": 8_000_000.0,
            "net_balance": -8_000_000.0,
        }
    ]

    result = _run_analysis(svc, {"start_date": _START, "end_date": _END})

    assert result["pnl_summary"]["total_ingresos"] == 8_000_000.0
    assert result["pnl_summary"]["utilidad_neta"] == 8_000_000.0


def test_mixed_entries():
    """Multiple account types → net profit calculated correctly."""
    svc = _mock_svc_empty()
    svc.get_balance_sheet.return_value = _BALANCE_DATA
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
    ]

    result = _run_analysis(svc, {"start_date": _START, "end_date": _END})

    assert result["pnl_summary"]["total_ingresos"] == 8_000_000.0
    assert result["pnl_summary"]["total_gastos"] == 2_000_000.0
    assert result["pnl_summary"]["total_costo_ventas"] == 4_000_000.0
    assert result["pnl_summary"]["utilidad_neta"] == 2_000_000.0


def test_period_filtering():
    """start_date and end_date are forwarded to the DB service."""
    svc = _mock_svc_empty()

    _run_analysis(svc, {"start_date": _START, "end_date": _END})

    # get_general_ledger is called twice: current period + previous period
    calls = svc.get_general_ledger.call_args_list
    current_call = calls[0]
    assert current_call.kwargs["start_date"].isoformat().startswith(_START)
    assert current_call.kwargs["end_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = _mock_svc_empty()

    _run_analysis(
        svc,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
    )

    assert (
        svc.get_general_ledger.call_args_list[0].kwargs["company_nit"] == _COMPANY_NIT
    )
    assert (
        svc.get_balance_sheet_for_period.call_args.kwargs["company_nit"] == _COMPANY_NIT
    )
