from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.graph import invoke_reporting_pipeline
from app.core.database import get_db
from app.services import db_service
from app.models.agent_outputs import (
    BalanceSheetOutput,
    CashFlowOutput,
    ComparativeReportOutput,
    FinancialAnalysisOutput,
    PnLOutput,
)

router = APIRouter()


def _build_params(
    start_date: Optional[date],
    end_date: Optional[date],
    include_analysis: bool = False,
) -> dict:
    params: dict = {}
    if start_date:
        params["start_date"] = start_date.isoformat()
    params["end_date"] = (end_date or date.today()).isoformat()
    if include_analysis:
        params["include_analysis"] = True
    return params


def _run_report(report_type: str, params: dict) -> dict:
    """Invoke the reporting pipeline and raise HTTP 500 on agent error."""
    result = invoke_reporting_pipeline(report_type=report_type, report_params=params)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result.get("report", {})


@router.get("/balance", response_model=BalanceSheetOutput)
async def get_balance_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    include_analysis: bool = Query(False, description="Add LLM narrative analysis"),
):
    """
    Balance General (Balance Sheet).
    Aggregates posted journal entries up to *end_date* grouped by PUC class.
    Returns assets, liabilities, equity, net profit and a balance-validation flag.
    Optionally includes LLM-powered analysis when include_analysis=true.
    """
    return _run_report("balance", _build_params(start_date, end_date, include_analysis))


@router.get("/pnl", response_model=PnLOutput)
async def get_pnl_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    include_analysis: bool = Query(False, description="Add LLM narrative analysis"),
):
    """
    Estado de Resultados (Profit & Loss).
    Aggregates revenue (class 4), COGS (class 6) and expenses (class 5)
    for the specified period. Optionally includes LLM-powered analysis.
    """
    return _run_report("pnl", _build_params(start_date, end_date, include_analysis))


@router.get("/cashflow", response_model=CashFlowOutput)
async def get_cashflow_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    include_analysis: bool = Query(False, description="Add LLM narrative analysis"),
):
    """
    Flujo de Caja (Cash Flow — direct method).
    Returns net balances of cash and bank accounts (class 11XX) for the period.
    Optionally includes LLM-powered analysis.
    """
    return _run_report(
        "cashflow", _build_params(start_date, end_date, include_analysis)
    )


@router.get("/analysis", response_model=FinancialAnalysisOutput)
async def get_financial_analysis(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
):
    """
    Análisis Financiero Integral.

    Generates a comprehensive financial analysis including:
    - Balance sheet and P&L summaries
    - Financial ratios (liquidity, profitability, leverage, efficiency)
    - Top accounts and terceros by volume
    - Anomaly detection (accounts with unusual balance changes)
    - Monthly trends (last 6 months)
    - 3-month financial predictions (linear regression + LLM interpretation)
    - LLM-generated executive summary, explanations, and recommendations

    The LLM analysis is non-fatal: if Gemini is unavailable, all deterministic
    data is still returned.
    """
    return _run_report("analysis", _build_params(start_date, end_date))


@router.get("/comparative", response_model=ComparativeReportOutput)
async def get_comparative_report(
    report_type: str = Query(
        ..., description="Report type to compare: balance, pnl, cashflow"
    ),
    period1_start: date = Query(..., description="Period 1 start date"),
    period1_end: date = Query(..., description="Period 1 end date"),
    period2_start: date = Query(..., description="Period 2 start date"),
    period2_end: date = Query(..., description="Period 2 end date"),
    db: Session = Depends(get_db),
):
    """
    Reporte Comparativo período vs período.

    Compares the same report type across two time periods and returns
    deltas (absolute and percentage change) per account. Direct DB query,
    does not invoke the LangGraph pipeline.
    """
    valid_types = {"balance", "pnl", "cashflow"}
    if report_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"report_type must be one of {sorted(valid_types)}",
        )

    p1s = datetime.combine(period1_start, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    p1e = datetime.combine(period1_end, datetime.max.time()).replace(
        tzinfo=timezone.utc
    )
    p2s = datetime.combine(period2_start, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    p2e = datetime.combine(period2_end, datetime.max.time()).replace(
        tzinfo=timezone.utc
    )

    comparison = db_service.get_period_comparison(db, p1s, p1e, p2s, p2e)
    comparison["report_type"] = f"comparative_{report_type}"
    comparison["generated_at"] = datetime.now(timezone.utc).isoformat()
    return comparison
