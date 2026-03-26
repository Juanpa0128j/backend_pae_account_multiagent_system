from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.graph import invoke_reporting_pipeline
from app.agents.tributario_agent import (
    TASA_ICA_DEFAULT,
    TASA_RENTA,
    _calc_period_renta_provision,
)
from app.core.database import SessionLocal
from app.models.agent_outputs import IVAOutput, WithholdingsOutput
from app.models.schemas import ICADeclaracionOutput, RentaProvisionOutput
from app.services import db_service

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _build_params(start_date: Optional[date], end_date: Optional[date]) -> dict:
    params = {}
    if start_date:
        params["start_date"] = start_date.isoformat()
    # Always include end_date so the query has an upper bound matching the
    # documented "default: today" behaviour.
    params["end_date"] = (end_date or date.today()).isoformat()
    return params


def _run_report(report_type: str, params: dict) -> dict:
    """Invoke the reporting pipeline and raise HTTP 500 on agent error."""
    result = invoke_reporting_pipeline(report_type=report_type, report_params=params)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result.get("report", {})


@router.get("/iva", response_model=IVAOutput)
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


@router.get("/withholdings", response_model=WithholdingsOutput)
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


@router.get("/ica", response_model=ICADeclaracionOutput)
async def get_ica_declaration(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
    nit: Optional[str] = Query(None, description="Company NIT to filter by"),
    db: Session = Depends(get_db),
):
    """
    Declaración ICA — Impuesto de Industria y Comercio.
    Aggregates PUC 4xxx credit entries from journal_entry_lines for the period
    and applies the company's tasa_ica (or national default 6.9‰).
    Ref: Ley 14/1983, Decreto 1333/1986.
    """
    period_end = end_date or date.today()
    from sqlalchemy import text as sql_text
    where_nit = "AND tercero_nit = :nit" if nit else ""
    where_start = "AND fecha >= :period_start" if start_date else ""
    params: dict = {"period_end": period_end, "nit": nit or ""}
    if start_date:
        params["period_start"] = start_date

    row = db.execute(sql_text(f"""
        SELECT COALESCE(SUM(credito), 0) AS ingresos
        FROM journal_entry_lines
        WHERE cuenta_puc >= '4000' AND cuenta_puc < '5000'
        {where_nit} {where_start}
        AND fecha <= :period_end
    """), params).fetchone()

    ingresos_brutos = Decimal(str(row.ingresos if row else 0))

    tasa_ica = TASA_ICA_DEFAULT
    if nit:
        settings = db_service.get_company_settings(db, nit)
        if settings:
            tasa_ica = Decimal(str(settings.tasa_ica))

    ica_a_pagar = (ingresos_brutos * tasa_ica).quantize(Decimal("0.01"))

    return ICADeclaracionOutput(
        period_start=start_date.isoformat() if start_date else None,
        period_end=period_end.isoformat(),
        generated_at=datetime.utcnow().isoformat(),
        ingresos_brutos=float(ingresos_brutos),
        tasa_ica=float(tasa_ica),
        ica_a_pagar=float(ica_a_pagar),
        referencias=[
            "Ley 14 de 1983 — Impuesto de Industria y Comercio",
            "Decreto 1333 de 1986 — Código de Régimen Municipal",
            "Art. 342 Ley 1955/2019 — territorialidad ICA",
        ],
    )


@router.get("/renta-provision", response_model=RentaProvisionOutput)
async def get_renta_provision(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(None, description="End date YYYY-MM-DD (default: today)"),
    nit: Optional[str] = Query(None, description="Company NIT to filter by"),
    db: Session = Depends(get_db),
):
    """
    Provisión Impuesto de Renta — tarifa general 35% (Art. 240 ET, Ley 2277/2022).
    Aggregates income (4xxx credits), costs (6xxx debits), and expenses (5xxx debits)
    from journal_entry_lines to compute net income and the corresponding tax provision.
    """
    period_end = end_date or date.today()

    tasa_renta = TASA_RENTA
    if nit:
        settings = db_service.get_company_settings(db, nit)
        if settings:
            tasa_renta = Decimal(str(settings.tasa_renta))

    result = _calc_period_renta_provision(
        db_session=db,
        nit_receptor=nit or "",
        period_start=start_date,
        period_end=period_end,
        tasa_renta=tasa_renta,
    )
    return RentaProvisionOutput(**result)
