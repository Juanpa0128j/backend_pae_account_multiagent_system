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
from typing import Dict, Optional

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


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    db: Session = Depends(get_db),
    company_nit: Optional[str] = Query(None, description="Filter by company NIT"),
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
