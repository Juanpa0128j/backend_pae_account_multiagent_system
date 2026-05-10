"""Tests for the notas a los estados financieros report builder."""

from unittest.mock import MagicMock


from app.services.report_builders.notas import build_notas

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
    """No balance data → resumen with zeros."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = {
        "assets": 0.0,
        "liabilities": 0.0,
        "equity": 0.0,
    }

    result = build_notas(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["resumen_financiero"]["activos"] == 0.0
    assert result["total_notas"] == 0
    assert result["report_type"] == "notas_eeff"


def test_single_account_type_only():
    """Balance with data → resumen populated."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = _BALANCE_DATA

    result = build_notas(None, {"start_date": _START, "end_date": _END}, svc)

    assert result["resumen_financiero"]["activos"] == 17_000_000.0
    assert result["resumen_financiero"]["pasivos"] == 5_000_000.0
    assert result["resumen_financiero"]["patrimonio"] == 10_000_000.0


def test_mixed_entries():
    """Notas content derived from RAG references."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = _BALANCE_DATA

    result = build_notas(None, {"start_date": _START, "end_date": _END}, svc)

    # notas list is built from RAG refs; with mocked RAG it may be empty
    assert isinstance(result["notas"], list)
    assert result["total_notas"] == len(result["notas"])


def test_period_filtering():
    """end_date is forwarded to the DB service as cutoff_date."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = {
        "assets": 0.0,
        "liabilities": 0.0,
        "equity": 0.0,
    }

    build_notas(None, {"start_date": _START, "end_date": _END}, svc)

    call = svc.get_balance_sheet.call_args
    assert call.kwargs["cutoff_date"].isoformat().startswith(_END)


def test_company_nit_filtering():
    """company_nit is forwarded to the DB service."""
    svc = MagicMock()
    svc.get_balance_sheet.return_value = {
        "assets": 0.0,
        "liabilities": 0.0,
        "equity": 0.0,
    }

    build_notas(
        None,
        {"start_date": _START, "end_date": _END, "company_nit": _COMPANY_NIT},
        svc,
    )

    call = svc.get_balance_sheet.call_args
    assert call.kwargs["company_nit"] == _COMPANY_NIT
