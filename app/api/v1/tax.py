from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.agents.graph import invoke_reporting_pipeline
from app.agents.tributario_agent import (
    TASA_ICA_DEFAULT,
    TASA_RENTA,
    _calc_ica,
    calc_period_renta_provision,
)
from app.core.database import get_db
from app.models.agent_outputs import IVAOutput, WithholdingsOutput
from app.models.schemas import ICADeclaracionOutput, RentaProvisionOutput
from app.services import db_service

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


@router.get("/iva", response_model=IVAOutput)
async def get_iva_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    include_analysis: bool = Query(False, description="Add LLM narrative analysis"),
):
    """
    Reporte IVA.
    Computes IVA generated (account 240808) vs. IVA deductible (account 240802)
    and returns the net IVA payable with applicable legal references.
    Optionally includes LLM-powered analysis when include_analysis=true.
    """
    return _run_report("iva", _build_params(start_date, end_date, include_analysis))


@router.get("/withholdings", response_model=WithholdingsOutput)
async def get_withholdings_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    include_analysis: bool = Query(False, description="Add LLM narrative analysis"),
):
    """
    Reporte Retenciones.
    Returns Retefuente (account 240815) and ReteICA (account 236540) balances
    with applicable legal references.
    Optionally includes LLM-powered analysis when include_analysis=true.
    """
    return _run_report(
        "withholdings", _build_params(start_date, end_date, include_analysis)
    )


@router.get("/ica", response_model=ICADeclaracionOutput)
async def get_ica_declaration(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    nit: Optional[str] = Query(
        None, description="Company NIT (nit_receptor) to filter by"
    ),
    db: Session = Depends(get_db),
):
    """
    Declaración ICA — Impuesto de Industria y Comercio.
    Aggregates PUC 4xxx credit entries for the period, filtered by company NIT
    (nit_receptor on transactions_posted), and applies the company's tasa_ica
    (or national default 6.9‰).
    Ref: Ley 14/1983, Decreto 1333/1986.
    """
    period_end = end_date or date.today()

    where_nit = "AND tp.nit_receptor = :nit" if nit else ""
    where_start = "AND j.fecha >= :period_start" if start_date else ""
    query_params: dict = {"period_end": period_end}
    if nit:
        query_params["nit"] = nit
    if start_date:
        query_params["period_start"] = start_date

    row = db.execute(
        sql_text(f"""
        SELECT COALESCE(SUM(j.credito), 0) AS ingresos
        FROM journal_entry_lines j
        JOIN transactions_posted tp ON j.transaction_posted_id = tp.id
        WHERE j.cuenta_puc >= '4000' AND j.cuenta_puc < '5000'
        {where_nit} {where_start}
        AND j.fecha <= :period_end
    """),
        query_params,
    ).fetchone()

    ingresos_brutos = Decimal(str(row.ingresos if row else 0))

    tasa_ica = TASA_ICA_DEFAULT
    if nit:
        settings = db_service.get_company_settings(db, nit)
        if settings and settings.tasa_ica:
            tasa_ica = Decimal(str(settings.tasa_ica))

    ica_a_pagar = _calc_ica(ingresos_brutos, tasa_ica)

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
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
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
        if settings and settings.tasa_renta:
            tasa_renta = Decimal(str(settings.tasa_renta))

    result = calc_period_renta_provision(
        db_session=db,
        nit_receptor=nit or "",
        period_start=start_date,
        period_end=period_end,
        tasa_renta=tasa_renta,
    )
    return RentaProvisionOutput(**result)
