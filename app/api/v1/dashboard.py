"""
Dashboard API — aggregated metrics for the frontend dashboard view.
Replaces mock data with real database queries.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Any, Dict, List

from app.core.database import get_db
from app.models.database import (
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from app.services import db_service

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
async def get_dashboard_stats(db: Session = Depends(get_db)):
    """
    Returns aggregated top-level metrics for the Dashboard view.
    Queries real data from the database.
    """
    # Pending documents
    pending_count = (
        db.query(func.count(TransactionPending.id))
        .filter(TransactionPending.status == TransactionStatus.PENDING)
        .scalar() or 0
    )

    # Transactions processed this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    processed_month = (
        db.query(func.count(TransactionPosted.id))
        .filter(TransactionPosted.created_at >= month_start)
        .scalar() or 0
    )

    # Active alerts (recent rejected transactions)
    alerts_count = (
        db.query(func.count(TransactionPending.id))
        .filter(TransactionPending.status == TransactionStatus.REJECTED)
        .scalar() or 0
    )

    # Balance sheet for financial totals
    balance = db_service.get_balance_sheet(db)

    # Cash position (class 11 accounts)
    ledger = db_service.get_general_ledger(db)
    efectivo = sum(
        float(r["total_debit"] - r["total_credit"])
        for r in ledger
        if r["account"].startswith("11")
    )

    # IVA payable
    iva_gen = next((r for r in ledger if r["account"] == "240808"), None)
    iva_desc = next((r for r in ledger if r["account"] == "240802"), None)
    iva_generado = float(iva_gen["total_credit"] - iva_gen["total_debit"]) if iva_gen else 0
    iva_descontable = float(iva_desc["total_debit"] - iva_desc["total_credit"]) if iva_desc else 0
    iva_por_pagar = iva_generado - iva_descontable

    # Total retenciones
    retfte_row = next((r for r in ledger if r["account"] == "240815"), None)
    retica_row = next((r for r in ledger if r["account"] == "236540"), None)
    retfte = float(retfte_row["total_credit"] - retfte_row["total_debit"]) if retfte_row else 0
    retica = float(retica_row["total_credit"] - retica_row["total_debit"]) if retica_row else 0

    # Transaction counts by status
    txn_counts = db_service.get_transaction_counts_by_status(db)

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
async def get_financial_summary(db: Session = Depends(get_db)):
    """
    Complete financial summary for the dashboard.
    Includes balance sheet totals, P&L, cash position, taxes, and recent activity.
    """
    balance = db_service.get_balance_sheet(db)
    ledger = db_service.get_general_ledger(db)

    # Cash
    efectivo = sum(
        float(r["total_debit"] - r["total_credit"])
        for r in ledger
        if r["account"].startswith("11")
    )

    # IVA
    iva_gen = next((r for r in ledger if r["account"] == "240808"), None)
    iva_desc = next((r for r in ledger if r["account"] == "240802"), None)
    iva_generado = float(iva_gen["total_credit"] - iva_gen["total_debit"]) if iva_gen else 0
    iva_descontable = float(iva_desc["total_debit"] - iva_desc["total_credit"]) if iva_desc else 0

    # Retenciones
    retfte_row = next((r for r in ledger if r["account"] == "240815"), None)
    retica_row = next((r for r in ledger if r["account"] == "236540"), None)
    retfte = float(retfte_row["total_credit"] - retfte_row["total_debit"]) if retfte_row else 0
    retica = float(retica_row["total_credit"] - retica_row["total_debit"]) if retica_row else 0

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

    txn_counts = db_service.get_transaction_counts_by_status(db)
    recent = db_service.get_recent_activity(db, limit=10)

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
