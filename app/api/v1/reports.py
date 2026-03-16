from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.agents.graph import invoke_reporting_pipeline

router = APIRouter()


def _build_params(start_date: Optional[date], end_date: Optional[date]) -> dict:
    params = {}
    if start_date:
        params["start_date"] = start_date.isoformat()
    if end_date:
        params["end_date"] = end_date.isoformat()
    return params


def _run_report(report_type: str, params: dict) -> dict:
    """Invoke the reporting pipeline and raise HTTP 500 on agent error."""
    result = invoke_reporting_pipeline(report_type=report_type, report_params=params)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result.get("report", {})


@router.get("/balance")
async def get_balance_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
):
    """
    Balance General (Balance Sheet).
    Aggregates posted journal entries up to *end_date* grouped by PUC class.
    Returns assets, liabilities, equity, net profit and a balance-validation flag.
    """
    return _run_report("balance", _build_params(start_date, end_date))


@router.get("/pnl")
async def get_pnl_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
):
    """
    Estado de Resultados (Profit & Loss).
    Aggregates revenue (class 4), COGS (class 6) and expenses (class 5)
    for the specified period.
    """
    return _run_report("pnl", _build_params(start_date, end_date))


@router.get("/cashflow")
async def get_cashflow_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
):
    """
    Flujo de Caja (Cash Flow — direct method).
    Returns net balances of cash and bank accounts (class 11XX) for the period.
    """
    return _run_report("cashflow", _build_params(start_date, end_date))
