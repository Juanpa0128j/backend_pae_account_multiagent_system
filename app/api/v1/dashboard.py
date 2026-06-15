"""
Dashboard API — aggregated metrics for the frontend dashboard view.
Replaces mock data with real database queries.
"""

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from app.core.auth import CurrentUser, get_current_user
from app.core.limiter import limiter
from app.core.database import get_db
from app.models.database import (
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from app.services import db_service, via_b_service
from app.services.nit_utils import normalize_nit

logger = logging.getLogger(__name__)

router = APIRouter()


def _q(value) -> float:
    """Quantize a numeric value to 2 decimals (DIAN/COP standard).

    Evita artefactos IEEE 754 como `57706.700000000004` cuando se suman
    floats acumulados. Convierte a Decimal, redondea con ROUND_HALF_UP,
    devuelve float (compatibilidad con Pydantic response models).
    """
    try:
        return float(
            Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
    except Exception:
        return 0.0


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
    # The period the KPIs reflect. For Vía B this is the most recent date
    # shared by balance + E.R. + libro_aux when ``period_resolution="common"``;
    # ``"partial"`` means each KPI may come from a different period. The
    # frontend should surface this so users know what they're looking at.
    period_end: Optional[str] = None
    period_resolution: Optional[str] = None  # 'common' | 'partial' | None


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


def _label_ym(ym: str) -> str:
    try:
        _, month = ym.split("-")
        return SPANISH_MONTHS.get(int(month), ym)
    except (ValueError, AttributeError):
        return ym


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
@limiter.limit("30/minute")
def get_dashboard_stats(
    request: Request,
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
            raise HTTPException(
                status_code=422, detail=f"El NIT de la empresa no es válido: {e}"
            )

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
        vb = via_b_service.get_dashboard_overrides(db, company_nit)
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
            period_end=vb.get("period_end"),
            period_resolution=vb.get("period_resolution"),
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

    # IVA payable — sumar todas las subcuentas 2408* (igual lógica que
    # /financial-summary y reportero_agent._build_iva).
    iva_generado = 0.0
    iva_descontable = 0.0
    for r in ledger:
        code = str(r.get("account") or "").strip()
        if not code.startswith("2408"):
            continue
        debit = float(r.get("total_debit") or 0)
        credit = float(r.get("total_credit") or 0)
        if code.startswith("240805"):
            iva_generado += credit
        elif code.startswith("240802") or code.startswith("240810"):
            iva_descontable += debit
        elif code == "2408":
            saldo_neto = debit - credit
            if saldo_neto > 0:
                iva_descontable += saldo_neto
            else:
                iva_generado += abs(saldo_neto)
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
        total_activos_cop=_q(balance["assets"]),
        total_pasivos_cop=_q(balance["liabilities"]),
        utilidad_neta_cop=_q(balance["net_profit"]),
        efectivo_disponible_cop=_q(efectivo),
        iva_por_pagar=_q(iva_por_pagar),
        total_retenciones=_q(retfte + retica),
        transacciones_por_estado=txn_counts,
        pathway=pathway,
    )


@router.get("/financial-summary", response_model=DashboardFinancialSummaryResponse)
@limiter.limit("30/minute")
def get_financial_summary(
    request: Request,
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
            raise HTTPException(
                status_code=422, detail=f"El NIT de la empresa no es válido: {e}"
            )

    balance = db_service.get_balance_sheet(db, company_nit=company_nit)
    ledger = db_service.get_general_ledger(db, company_nit=company_nit)

    # Cash
    efectivo = sum(
        float(r["total_debit"] - r["total_credit"])
        for r in ledger
        if r["account"].startswith("11")
    )

    # IVA — sumar todas las subcuentas 2408* (240802 descontable, 240805 generado,
    # 240810 retenido, o cuenta padre 2408). Diferenciar por naturaleza de saldo
    # cuando solo aparece el padre.
    iva_generado = 0.0
    iva_descontable = 0.0
    for r in ledger:
        code = str(r.get("account") or "").strip()
        if not code.startswith("2408"):
            continue
        debit = float(r.get("total_debit") or 0)
        credit = float(r.get("total_credit") or 0)
        if code.startswith("240805"):
            iva_generado += credit
        elif code.startswith("240802") or code.startswith("240810"):
            iva_descontable += debit
        elif code == "2408":
            saldo_neto = debit - credit
            if saldo_neto > 0:
                iva_descontable += saldo_neto
            else:
                iva_generado += abs(saldo_neto)

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
        total_activos=_q(balance["assets"]),
        total_pasivos=_q(balance["liabilities"]),
        patrimonio=_q(balance["equity"]),
        utilidad_neta=_q(balance["net_profit"]),
        efectivo_disponible=_q(efectivo),
        iva_por_pagar=_q(iva_generado - iva_descontable),
        total_retenciones=_q(retfte + retica),
        ingresos_periodo=_q(ingresos),
        gastos_periodo=_q(gastos),
        transacciones_por_estado=txn_counts,
        actividad_reciente=recent,
    )


@router.get("/monthly-trend", response_model=MonthlyTrendResponse)
@limiter.limit("30/minute")
def get_monthly_trend(
    request: Request,
    db: Session = Depends(get_db),
    company_nit: Optional[str] = Query(None, description="Filter by company NIT"),
    months: int = Query(6, ge=1, le=24, description="Number of months to look back"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Returns monthly ingresos vs gastos for the last N months.
    Used to power the Tendencia bar chart on the dashboard.

    Vía B companies aggregate over uploaded estado_resultados periods instead
    of journal_entry_lines; an empty series is returned when fewer than two
    P&L statements are on file (a trend needs at least two points).
    """
    if company_nit:
        try:
            company_nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(
                status_code=422, detail=f"El NIT de la empresa no es válido: {e}"
            )

        try:
            pathway = db_service.get_company_locked_pathway(db, company_nit)
        except Exception:
            pathway = None
        if pathway == "work_with_existing":
            via_b_trend = via_b_service.get_monthly_trend(db, company_nit, months)
            if via_b_trend is None:
                return MonthlyTrendResponse(data=[])
            return MonthlyTrendResponse(
                data=[
                    MonthlyTrendPoint(
                        month=_label_ym(point["month"]),
                        ingresos=point["ingresos"],
                        gastos=point["gastos"],
                    )
                    for point in via_b_trend["data"]
                ]
            )

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

    data = [
        MonthlyTrendPoint(
            month=_label_ym(ym),
            ingresos=ingresos_by_month.get(ym, 0.0),
            gastos=gastos_by_month.get(ym, 0.0),
        )
        for ym in all_months
    ]

    return MonthlyTrendResponse(data=data)
