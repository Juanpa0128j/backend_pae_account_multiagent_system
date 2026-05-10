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
    FinancialStatement,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from app.services import db_service
from app.services.nit_utils import normalize_nit
from app.services.parse_utils import safe_float

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
    # Vía-aware fields — clients can switch the KPI cards based on `pathway`.
    pathway: Optional[str] = None  # 'build_from_scratch' | 'work_with_existing' | None
    via_b_statements_count: int = 0
    latest_via_b_period: Optional[str] = None
    derivation_ready: bool = False


def _via_b_dashboard_overrides(db: Session, company_nit: str) -> Dict[str, Any]:
    """Compute Vía B financial totals from FinancialStatement rows.

    Returns a dict with the same keys the Vía A flow computes from journal
    entries (total_activos, total_pasivos, etc.) plus Vía B metadata
    (statements_count, latest_period, derivation_ready).
    """
    rows = (
        db.query(FinancialStatement)
        .filter(FinancialStatement.entity_nit == company_nit)
        .order_by(FinancialStatement.period_end.desc())
        .all()
    )
    bg = next((r for r in rows if r.statement_type == "balance_general"), None)
    er = next((r for r in rows if r.statement_type == "estado_resultados"), None)
    la = next((r for r in rows if r.statement_type == "libro_auxiliar"), None)

    def _f(d: Optional[dict], key: str) -> float:
        if not isinstance(d, dict):
            return 0.0
        try:
            return float(d.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    bg_data = bg.data if bg and isinstance(bg.data, dict) else {}
    er_data = er.data if er and isinstance(er.data, dict) else {}

    total_activos = _f(bg_data, "total_activos")
    total_pasivos = _f(bg_data, "total_pasivos")
    utilidad_neta = _f(er_data, "utilidad_neta")

    # Efectivo from libro_auxiliar lines on PUC class 11 if available.
    efectivo = 0.0
    if la and isinstance(la.data, dict):
        lines = la.data.get("lines") or la.data.get("accounts") or []
        if isinstance(lines, list):
            for line in lines:
                if not isinstance(line, dict):
                    continue
                code = str(line.get("cuenta_puc") or line.get("codigo") or "")
                if code.startswith("11"):
                    efectivo += safe_float(line.get("debito")) - safe_float(
                        line.get("credito")
                    )

    # `derivation_ready` matches the logic in /reports/derivation/status: all 3
    # source statement types must share at least one common period_end (a single
    # statement of each type isn't enough if the periods don't line up).
    direct = [r for r in rows if r.source_mode == "direct"]
    required_types = ("balance_general", "estado_resultados", "libro_auxiliar")
    period_ends_by_type: Dict[str, set] = {t: set() for t in required_types}
    for r in direct:
        if r.statement_type in period_ends_by_type and r.period_end is not None:
            period_ends_by_type[r.statement_type].add(r.period_end)
    common_period_ends = (
        set.intersection(*period_ends_by_type.values())
        if all(period_ends_by_type[t] for t in required_types)
        else set()
    )
    derivation_ready = bool(common_period_ends)
    latest = max((r.period_end for r in direct if r.period_end), default=None)

    return {
        "total_activos": total_activos,
        "total_pasivos": total_pasivos,
        "utilidad_neta": utilidad_neta,
        "efectivo": efectivo,
        "statements_count": len(direct),
        "latest_period": latest.isoformat() if latest else None,
        "derivation_ready": derivation_ready,
    }


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

    # Detect pathway so we can branch to Vía B-aware figures.
    pathway: Optional[str] = None
    if company_nit:
        try:
            pathway = db_service.get_company_locked_pathway(db, company_nit)
        except Exception:
            pathway = None

    # ── Vía B branch ────────────────────────────────────────────────────────
    # Source financial figures from FinancialStatement rows; transaction
    # counters stay 0 because Vía B doesn't produce TransactionPending rows.
    if pathway == "work_with_existing" and company_nit:
        vb = _via_b_dashboard_overrides(db, company_nit)
        return DashboardStatsResponse(
            documentos_pendientes=0,
            transacciones_procesadas_mes=0,
            alertas_activas=0,
            total_activos_cop=vb["total_activos"],
            total_pasivos_cop=vb["total_pasivos"],
            utilidad_neta_cop=vb["utilidad_neta"],
            efectivo_disponible_cop=vb["efectivo"],
            iva_por_pagar=0.0,
            total_retenciones=0.0,
            transacciones_por_estado={},
            pathway=pathway,
            via_b_statements_count=vb["statements_count"],
            latest_via_b_period=vb["latest_period"],
            derivation_ready=vb["derivation_ready"],
        )

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
        pathway=pathway,
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
