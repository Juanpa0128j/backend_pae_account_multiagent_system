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


@router.get("/iva")
async def get_iva_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
):
    """
    Reporte IVA.
    Computes IVA generated (account 240808) vs. IVA deductible (account 240802)
    and returns the net IVA payable with applicable legal references.
    """
    return _run_report("iva", _build_params(start_date, end_date))


@router.get("/withholdings")
async def get_withholdings_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
):
    """
    Reporte Retenciones.
    Returns Retefuente (account 240815) and ReteICA (account 236540) balances
    with applicable legal references.
    """
    return _run_report("withholdings", _build_params(start_date, end_date))
