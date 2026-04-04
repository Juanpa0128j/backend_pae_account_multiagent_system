from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.graph import invoke_reporting_pipeline
from app.core.database import SessionLocal
from app.models.agent_outputs import BalanceSheetOutput, CashFlowOutput, PnLOutput
from app.models.database import FinancialStatement
from app.services.financial_statement_service import list_financial_statements
from app.services.nit_utils import normalize_nit

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


def _run_report(report_type: str, params: dict, company_nit: Optional[str]) -> dict:
    """Invoke the reporting pipeline and raise HTTP 500 on agent error."""
    normalized_company_nit = None
    if company_nit:
        try:
            normalized_company_nit = normalize_nit(company_nit)
        except ValueError as nit_err:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {nit_err}")

    result = invoke_reporting_pipeline(
        report_type=report_type,
        report_params=params,
        company_nit=normalized_company_nit,
    )
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result.get("report", {})


@router.get("/balance", response_model=BalanceSheetOutput)
async def get_balance_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
):
    """
    Balance General (Balance Sheet).
    Aggregates posted journal entries up to *end_date* grouped by PUC class.
    Returns assets, liabilities, equity, net profit and a balance-validation flag.
    """
    return _run_report("balance", _build_params(start_date, end_date), company_nit)


@router.get("/pnl", response_model=PnLOutput)
async def get_pnl_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
):
    """
    Estado de Resultados (Profit & Loss).
    Aggregates revenue (class 4), COGS (class 6) and expenses (class 5)
    for the specified period. Optionally includes LLM-powered analysis.
    """
    return _run_report("pnl", _build_params(start_date, end_date), company_nit)


@router.get("/cashflow", response_model=CashFlowOutput)
async def get_cashflow_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
):
    """
    Flujo de Caja (Cash Flow — direct method).
    Returns net balances of cash and bank accounts (class 11XX) for the period.
    Optionally includes LLM-powered analysis.
    """
    return _run_report("cashflow", _build_params(start_date, end_date), company_nit)


@router.get("/statements")
async def get_financial_statements(
    company_nit: str = Query(..., description="Company NIT"),
    statement_type: Optional[str] = Query(None, description="Filter by type (e.g. flujo_de_caja)"),
    start_date: Optional[date] = Query(None, description="Period start YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="Period end YYYY-MM-DD"),
    source_mode: Optional[str] = Query(None, description="Filter: direct | derived | derived_from_journal"),
):
    """List stored FinancialStatement records for a company."""
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    period_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc) if start_date else None
    period_end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc) if end_date else None

    return list_financial_statements(
        company_nit=normalized_nit,
        period_start=period_start,
        period_end=period_end,
        statement_type=statement_type,
        source_mode=source_mode,
    )


@router.get("/statements/{statement_id}")
async def get_financial_statement_by_id(statement_id: str):
    """Get a specific FinancialStatement by ID."""
    db = SessionLocal()
    try:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.id == statement_id).first()
        if stmt is None:
            raise HTTPException(status_code=404, detail=f"Statement {statement_id} not found")
        return {
            "id": stmt.id,
            "ingest_id": stmt.ingest_id,
            "statement_type": stmt.statement_type,
            "period_start": stmt.period_start.isoformat() if stmt.period_start else None,
            "period_end": stmt.period_end.isoformat() if stmt.period_end else None,
            "entity_nit": stmt.entity_nit,
            "source_mode": stmt.source_mode,
            "data": stmt.data,
            "created_at": stmt.created_at.isoformat() if stmt.created_at else None,
        }
    finally:
        db.close()
