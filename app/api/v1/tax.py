from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.agents.graph import invoke_reporting_pipeline
from app.agents.tributario_agent import (
    TASA_ICA_DEFAULT,
    TASA_RENTA,
    _calc_ica,
    calc_period_renta_provision,
)
from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.models.agent_outputs import IVAOutput, WithholdingsOutput
from app.models.schemas import ICADeclaracionOutput, RentaProvisionOutput
from app.services import db_service
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
            raise HTTPException(
                status_code=422, detail=f"Invalid company_nit: {nit_err}"
            )

    result = invoke_reporting_pipeline(
        report_type=report_type,
        report_params=params,
        company_nit=normalized_company_nit,
    )
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result.get("report", {})


@router.get("/iva", response_model=IVAOutput)
async def get_iva_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Reporte IVA.
    Computes IVA generated (account 240808) vs. IVA deductible (account 240802)
    and returns the net IVA payable with applicable legal references.
    """
    return _run_report("iva", _build_params(start_date, end_date), company_nit)


@router.get("/withholdings", response_model=WithholdingsOutput)
async def get_withholdings_report(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Reporte Retenciones.
    Returns Retefuente (account 2365) and ReteICA (account 2368) balances
    with applicable legal references.
    Optionally includes LLM-powered analysis when include_analysis=true.
    """
    return _run_report("withholdings", _build_params(start_date, end_date), company_nit)


@router.get("/ica", response_model=ICADeclaracionOutput)
async def get_ica_declaration(
    start_date: Optional[date] = Query(None, description="Start date YYYY-MM-DD"),
    end_date: Optional[date] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    company_nit: Optional[str] = Query(
        None, description="Company NIT (nit_receptor) to filter by"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Declaración ICA — Impuesto de Industria y Comercio.
    Aggregates PUC 4xxx credit entries for the period, filtered by company NIT
    (nit_receptor on transactions_posted), and applies the company's tasa_ica
    (or national default 6.9‰).
    Ref: Ley 14/1983, Decreto 1333/1986.
    """
    period_end = end_date or date.today()

    where_nit = "AND tp.nit_receptor = :nit" if company_nit else ""
    where_start = "AND j.fecha >= :period_start" if start_date else ""
    query_params: dict = {"period_end": period_end}
    if company_nit:
        query_params["nit"] = company_nit
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
    if company_nit:
        settings = db_service.get_company_settings(db, company_nit)
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
    company_nit: Optional[str] = Query(None, description="Company NIT to filter by"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Provisión Impuesto de Renta — tarifa general 35% (Art. 240 ET, Ley 2277/2022).
    Aggregates income (4xxx credits), costs (6xxx debits), and expenses (5xxx debits)
    from journal_entry_lines to compute net income and the corresponding tax provision.
    """
    period_end = end_date or date.today()

    tasa_renta = TASA_RENTA
    if company_nit:
        settings = db_service.get_company_settings(db, company_nit)
        if settings and settings.tasa_renta:
            tasa_renta = Decimal(str(settings.tasa_renta))

    result = calc_period_renta_provision(
        db_session=db,
        nit_receptor=company_nit or "",
        period_start=start_date,
        period_end=period_end,
        tasa_renta=tasa_renta,
    )
    return RentaProvisionOutput(**result)


# ---------------------------------------------------------------------------
# Tax Declaration Drafts
# ---------------------------------------------------------------------------

from app.services.tax_declaration_service import (  # noqa: E402
    FieldNotEditableError,
    FieldNotFoundError,
    generate_declaration_draft,
    get_draft,
    update_draft_field,
)
from app.services.tax_calendar_service import (  # noqa: E402
    SUPPORTED_IVA_REGIMES,
    SUPPORTED_YEARS,
    list_obligations,
)
from app.services.certificate_service import generate_f220_certificates  # noqa: E402
from app.services.exogena_service import (  # noqa: E402
    generate_formato_1001,
    generate_formato_2276,
)


class GenerateDraftRequest(BaseModel):
    company_nit: str
    form_type: str
    period_start: date
    period_end: date


class UpdateFieldRequest(BaseModel):
    renglon: str
    value: float


@router.post(
    "/declarations/generate", summary="Generate pre-filled DIAN declaration draft"
)
def api_generate_draft(
    body: GenerateDraftRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate a pre-filled declaration draft (F300, F350, F110, ICA).

    Returns the draft with all renglones pre-populated from journal entries.
    Fields with requires_review=True need accountant review before filing.

    Disclaimer: Drafts are for accountant review only (Ley 43/1990).
    """
    try:
        draft = generate_declaration_draft(
            db=db,
            company_nit=body.company_nit,
            form_type=body.form_type,
            period_start=body.period_start,
            period_end=body.period_end,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "draft_id": draft.id,
        "company_nit": draft.company_nit,
        "form_type": draft.form_type,
        "period_start": draft.period_start,
        "period_end": draft.period_end,
        "year": draft.year,
        "status": draft.status,
        "fields": draft.fields_json,
        "warnings": draft.warnings_json,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
    }


@router.get("/declarations/{draft_id}", summary="Get a declaration draft")
def api_get_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve a draft by ID including all pre-filled renglones and warnings."""
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    return {
        "draft_id": draft.id,
        "company_nit": draft.company_nit,
        "form_type": draft.form_type,
        "period_start": draft.period_start,
        "period_end": draft.period_end,
        "year": draft.year,
        "status": draft.status,
        "fields": draft.fields_json,
        "warnings": draft.warnings_json,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


@router.patch(
    "/declarations/{draft_id}/fields", summary="Update a requires_review field"
)
def api_update_draft_field(
    draft_id: str,
    body: UpdateFieldRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Accountant updates a field value (requires_review=True fields).
    After update the field is marked requires_review=False.
    """
    try:
        draft = update_draft_field(db, draft_id, body.renglon, body.value)
    except FieldNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FieldNotEditableError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    return {
        "draft_id": draft.id,
        "status": draft.status,
        "fields": draft.fields_json,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


@router.get("/calendar", summary="DIAN 2026 tax calendar with deadlines")
def api_tax_calendar(
    nit: str = Query(..., description="Company NIT (without DV)"),
    year: int = Query(2026, description="Tax year"),
    iva_regime: str = Query("bimestral", description="bimestral | cuatrimestral"),
    alert_days: int = Query(30, description="Days-until threshold for alert flag"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Return the full tax obligation calendar for 2026 sorted by deadline.

    Each entry includes form_type, period, deadline (ISO date), days_until,
    and alert=True if the deadline is within alert_days.

    Source: Calendario Tributario DIAN 2026.
    """
    if year not in SUPPORTED_YEARS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported year: {year}. Supported: {sorted(SUPPORTED_YEARS)}",
        )
    if iva_regime not in SUPPORTED_IVA_REGIMES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported iva_regime: {iva_regime!r}. "
                f"Must be one of {sorted(SUPPORTED_IVA_REGIMES)}"
            ),
        )

    today = date.today()
    try:
        entries = list_obligations(
            nit=nit,
            year=year,
            iva_regime=iva_regime,
            alert_days=alert_days,
            today=today,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "nit": nit,
        "year": year,
        "iva_regime": iva_regime,
        "generated_at": today.isoformat(),
        "obligations": [
            {
                "form_type": e.form_type,
                "period": e.period,
                "period_label": e.period_label,
                "deadline": e.deadline.isoformat(),
                "days_until": e.days_until,
                "alert": e.alert,
            }
            for e in entries
        ],
    }


@router.post(
    "/certificates/f220",
    summary="Generate F220 retention certificates for all terceros",
)
def api_generate_f220(
    company_nit: str = Query(..., description="Company NIT (retenedor)"),
    year: int = Query(..., description="Tax year (e.g. 2025)"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate F220 Certificado de Retención en la Fuente for every tercero
    that received payments subject to Retefuente or ReteICA during the year.

    Returns one certificate per tercero. Fields marked requires_review=True
    need accountant action before delivery (Art. 381 ET, Ley 43/1990).
    """
    try:
        certs = generate_f220_certificates(db=db, company_nit=company_nit, year=year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "company_nit": company_nit,
        "year": year,
        "total_certificates": len(certs),
        "certificates": [c.to_dict() for c in certs],
    }


_EXOGENA_GENERATORS = {
    "1001": generate_formato_1001,
    "2276": generate_formato_2276,
}


@router.get(
    "/exogena/{formato}",
    summary="Generate DIAN exógena (medios magnéticos) data",
)
def api_exogena(
    formato: str,
    company_nit: str = Query(..., description="Reporting company NIT"),
    year: int = Query(..., description="Tax year (e.g. 2025)"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate normalized exógena data for DIAN medios magnéticos.

    Supported formats:
      - 1001: Pagos o abonos en cuenta y retenciones practicadas
      - 2276: Ingresos recibidos por personas naturales/jurídicas

    All NIT values are digit-only; names are UPPERCASE with accents removed
    and Ñ→N per DIAN strict normalization (Resolución 000162/2023).
    Amounts are integer pesos.

    Fields with submission_ready=False have validation_errors that must be
    corrected before DIAN submission.
    """
    generator = _EXOGENA_GENERATORS.get(formato)
    if not generator:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{formato}' no soportado. Use: {list(_EXOGENA_GENERATORS)}",
        )

    try:
        rows = generator(db=db, company_nit=company_nit, year=year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    invalid_count = sum(1 for r in rows if not r.get("submission_ready", True))
    return {
        "formato": formato,
        "company_nit": company_nit,
        "year": year,
        "total_rows": len(rows),
        "invalid_rows": invalid_count,
        "rows": rows,
    }
