"""
Dashboard API — aggregated metrics for the frontend dashboard view.
Replaces mock data with real database queries.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.models.database import (
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from app.services import db_service
from app.services.nit_utils import normalize_nit

logger = logging.getLogger(__name__)

router = APIRouter()


class DashboardStatsResponse(BaseModel):
    documentos_pendientes: int = 0
    transacciones_procesadas_mes: int = 0
    alertas_activas: int = 0
    total_activos_cop: float = 0.0
    total_pasivos_cop: float = 0.0
    utilidad_neta_cop: float = 0.0
    efectivo_disponible_cop: float = 0.0
    iva_por_pagar: float = 0.0
    total_retenciones: float = 0.0
    transacciones_por_estado: Dict[str, int] = Field(default_factory=dict)


SPANISH_MONTHS = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}


class MonthlyTrendPoint(BaseModel):
    month: str
    ingresos: float
    gastos: float


class MonthlyTrendResponse(BaseModel):
    data: List[MonthlyTrendPoint]


class DashboardFinancialSummaryResponse(BaseModel):
    total_activos: float = 0.0
    total_pasivos: float = 0.0
    patrimonio: float = 0.0
    utilidad_neta: float = 0.0
    efectivo_disponible: float = 0.0
    iva_por_pagar: float = 0.0
    total_retenciones: float = 0.0
    ingresos_periodo: float = 0.0
    gastos_periodo: float = 0.0
    transacciones_por_estado: Dict[str, int] = Field(default_factory=dict)
    actividad_reciente: List[Dict[str, Any]] = Field(default_factory=list)


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    db: Session = Depends(get_db),
    company_nit: Optional[str] = Query(None, description="Filter by company NIT"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Returns aggregated top-level metrics for the Dashboard view.
    Queries real data from the database.
    """
    if company_nit:
        try:
            company_nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    # Pending documents
    pending_q = db.query(func.count(TransactionPending.id)).filter(
        TransactionPending.status == TransactionStatus.PENDING
    )
    if company_nit:
        pending_q = pending_q.filter(TransactionPending.company_nit == company_nit)
    pending_count = pending_q.scalar() or 0

    # Transactions processed this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    processed_q = db.query(func.count(TransactionPosted.id)).filter(
        TransactionPosted.created_at >= month_start
    )
    if company_nit:
        processed_q = processed_q.filter(TransactionPosted.company_nit == company_nit)
    processed_month = processed_q.scalar() or 0

    # Active alerts (recent rejected transactions)
    alerts_q = db.query(func.count(TransactionPending.id)).filter(
        TransactionPending.status == TransactionStatus.REJECTED
    )
    if company_nit:
        alerts_q = alerts_q.filter(TransactionPending.company_nit == company_nit)
    alerts_count = alerts_q.scalar() or 0

    # Balance sheet for financial totals
    balance = db_service.get_balance_sheet(db, company_nit=company_nit)

    # Cash position (class 11 accounts)
    ledger = db_service.get_general_ledger(db, company_nit=company_nit)
    efectivo = sum(
        float(r["total_debit"] - r["total_credit"])
        for r in ledger
        if r["account"].startswith("11")
    )

    # IVA payable
    iva_gen = next((r for r in ledger if r["account"] == "240808"), None)
    iva_desc = next((r for r in ledger if r["account"] == "240802"), None)
    iva_generado = (
        float(iva_gen["total_credit"] - iva_gen["total_debit"]) if iva_gen else 0
    )
    iva_descontable = (
        float(iva_desc["total_debit"] - iva_desc["total_credit"]) if iva_desc else 0
    )
    iva_por_pagar = iva_generado - iva_descontable

    # Total retenciones
    retfte_row = next(
        (r for r in ledger if r["account"] == "2365"), None
    )  # Retefuente por pagar — PUC 2026
    retica_row = next(
        (r for r in ledger if r["account"] == "2368"), None
    )  # ReteICA por pagar — PUC 2026
    retfte = (
        float(retfte_row["total_credit"] - retfte_row["total_debit"])
        if retfte_row
        else 0
    )
    retica = (
        float(retica_row["total_credit"] - retica_row["total_debit"])
        if retica_row
        else 0
    )

    # Transaction counts by status
    txn_counts = db_service.get_transaction_counts_by_status(
        db, company_nit=company_nit
    )

    return DashboardStatsResponse(
        documentos_pendientes=pending_count,
        transacciones_procesadas_mes=processed_month,
        alertas_activas=alerts_count,
        total_activos_cop=balance["assets"],
        total_pasivos_cop=balance["liabilities"],
        utilidad_neta_cop=balance["net_profit"],
        efectivo_disponible_cop=efectivo,
        iva_por_pagar=iva_por_pagar,
        total_retenciones=retfte + retica,
        transacciones_por_estado=txn_counts,
    )


@router.get("/financial-summary", response_model=DashboardFinancialSummaryResponse)
async def get_financial_summary(
    db: Session = Depends(get_db),
    company_nit: Optional[str] = Query(None, description="Filter by company NIT"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Complete financial summary for the dashboard.
    Includes balance sheet totals, P&L, cash position, taxes, and recent activity.
    """
    if company_nit:
        try:
            company_nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    balance = db_service.get_balance_sheet(db, company_nit=company_nit)
    ledger = db_service.get_general_ledger(db, company_nit=company_nit)

    # Cash
    efectivo = sum(
        float(r["total_debit"] - r["total_credit"])
        for r in ledger
        if r["account"].startswith("11")
    )

    # IVA
    iva_gen = next((r for r in ledger if r["account"] == "240808"), None)
    iva_desc = next((r for r in ledger if r["account"] == "240802"), None)
    iva_generado = (
        float(iva_gen["total_credit"] - iva_gen["total_debit"]) if iva_gen else 0
    )
    iva_descontable = (
        float(iva_desc["total_debit"] - iva_desc["total_credit"]) if iva_desc else 0
    )

    # Retenciones (PUC 2026)
    retfte_row = next((r for r in ledger if r["account"] == "2365"), None)
    retica_row = next((r for r in ledger if r["account"] == "2368"), None)
    retfte = (
        float(retfte_row["total_credit"] - retfte_row["total_debit"])
        if retfte_row
        else 0
    )
    retica = (
        float(retica_row["total_credit"] - retica_row["total_debit"])
        if retica_row
        else 0
    )

    # Revenue and expenses for period
    ingresos = sum(
        float(r["total_credit"] - r["total_debit"])
        for r in ledger
        if r["account"].startswith("4")
    )
    gastos = sum(
        float(r["total_debit"] - r["total_credit"])
        for r in ledger
        if r["account"].startswith("5")
    )

    txn_counts = db_service.get_transaction_counts_by_status(
        db, company_nit=company_nit
    )
    recent = db_service.get_recent_activity(db, limit=10, company_nit=company_nit)

    return DashboardFinancialSummaryResponse(
        total_activos=balance["assets"],
        total_pasivos=balance["liabilities"],
        patrimonio=balance["equity"],
        utilidad_neta=balance["net_profit"],
        efectivo_disponible=efectivo,
        iva_por_pagar=iva_generado - iva_descontable,
        total_retenciones=retfte + retica,
        ingresos_periodo=ingresos,
        gastos_periodo=gastos,
        transacciones_por_estado=txn_counts,
        actividad_reciente=recent,
    )


@router.get("/monthly-trend", response_model=MonthlyTrendResponse)
async def get_monthly_trend(
    db: Session = Depends(get_db),
    company_nit: Optional[str] = Query(None, description="Filter by company NIT"),
    months: int = Query(6, ge=1, le=24, description="Number of months to look back"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Returns monthly ingresos vs gastos for the last N months.
    Used to power the Tendencia bar chart on the dashboard.
    """
    if company_nit:
        try:
            company_nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    totals = db_service.get_monthly_totals_by_class(
        db, months=months, company_nit=company_nit
    )

    ingresos_by_month: dict[str, float] = {
        row["month"]: float(row.get("total_credit", 0) - row.get("total_debit", 0))
        for row in totals.get("ingresos", [])
    }
    gastos_by_month: dict[str, float] = {
        row["month"]: float(row.get("total_debit", 0) - row.get("total_credit", 0))
        for row in totals.get("gastos", [])
    }

    all_months = sorted(set(ingresos_by_month) | set(gastos_by_month))

    def _label(ym: str) -> str:
        try:
            year, month = ym.split("-")
            return SPANISH_MONTHS.get(int(month), ym)
        except (ValueError, AttributeError):
            return ym

    data = [
        MonthlyTrendPoint(
            month=_label(ym),
            ingresos=ingresos_by_month.get(ym, 0.0),
            gastos=gastos_by_month.get(ym, 0.0),
        )
        for ym in all_months
    ]

    return MonthlyTrendResponse(data=data)
