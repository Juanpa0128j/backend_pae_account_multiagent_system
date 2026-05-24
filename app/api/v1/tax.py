import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

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
from app.models.schemas import (
    BaseMinimaUpsertRequest,
    FileDraftRequest,
    ICADeclaracionOutput,
    PerdidaFiscalResponse,
    PerdidaFiscalUpsertRequest,
    RentaProvisionOutput,
    ReopenDraftRequest,
    TarifaRentaResponse,
    TarifaRentaUpsertRequest,
    TaxConstantsResponse,
    UvtUpsertRequest,
    VALID_CONCEPTO_VALUES,
)
from app.services import db_service
from app.services.nit_utils import normalize_nit

router = APIRouter()


def _resolve_period(
    period_start: Optional[date],
    period_end: Optional[date],
) -> Tuple[date, date]:
    """Return (start, end). Both None → current month. Partial → 400."""
    if period_start is None and period_end is None:
        today = date.today()
        first_day = today.replace(day=1)
        last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return first_day, last_day
    if period_start is None or period_end is None:
        raise HTTPException(
            status_code=400,
            detail="Debe enviar period_start y period_end juntos",
        )
    if period_end < period_start:
        raise HTTPException(
            status_code=400,
            detail="period_end no puede ser anterior a period_start",
        )
    return period_start, period_end


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
    period_start: Optional[date] = Query(
        None,
        description="Inicio del período YYYY-MM-DD (default: primer día del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del período YYYY-MM-DD (default: último día del mes actual)",
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Reporte IVA.
    Computes IVA generated (account 240808) vs. IVA deductible (account 240802)
    and returns the net IVA payable with applicable legal references.
    """
    start, end = _resolve_period(period_start, period_end)
    return _run_report("iva", _build_params(start, end), company_nit)


@router.get("/withholdings", response_model=WithholdingsOutput)
async def get_withholdings_report(
    period_start: Optional[date] = Query(
        None,
        description="Inicio del período YYYY-MM-DD (default: primer día del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del período YYYY-MM-DD (default: último día del mes actual)",
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
    start, end = _resolve_period(period_start, period_end)
    return _run_report("withholdings", _build_params(start, end), company_nit)


@router.get("/ica", response_model=ICADeclaracionOutput)
async def get_ica_declaration(
    period_start: Optional[date] = Query(
        None,
        description="Inicio del período YYYY-MM-DD (default: primer día del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del período YYYY-MM-DD (default: último día del mes actual)",
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
    start, end = _resolve_period(period_start, period_end)

    where_nit = "AND tp.company_nit = :nit" if company_nit else ""
    where_start = "AND j.fecha >= :period_start"
    query_params: dict = {"period_end": end, "period_start": start}
    if company_nit:
        query_params["nit"] = company_nit

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
    cuenta_pasivo = "2368"
    if company_nit:
        settings = db_service.get_company_settings(db, company_nit)
        if settings and settings.tasa_ica:
            tasa_ica = Decimal(str(settings.tasa_ica))
        if settings and settings.cuenta_ica_propio:
            cuenta_pasivo = settings.cuenta_ica_propio

    ica_a_pagar = _calc_ica(ingresos_brutos, tasa_ica)

    return ICADeclaracionOutput(
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        generated_at=datetime.utcnow().isoformat(),
        ingresos_brutos=float(ingresos_brutos),
        tasa_ica=float(tasa_ica),
        ica_a_pagar=float(ica_a_pagar),
        cuenta_pasivo_puc=cuenta_pasivo,
        referencias=[
            "Ley 14 de 1983 — Impuesto de Industria y Comercio",
            "Decreto 1333 de 1986 — Código de Régimen Municipal",
            "Art. 342 Ley 1955/2019 — territorialidad ICA",
        ],
    )


@router.get("/renta-provision", response_model=RentaProvisionOutput)
async def get_renta_provision(
    period_start: Optional[date] = Query(
        None,
        description="Inicio del período YYYY-MM-DD (default: primer día del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del período YYYY-MM-DD (default: último día del mes actual)",
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
    start, end = _resolve_period(period_start, period_end)
    year = end.year

    tasa_renta = TASA_RENTA
    settings = None
    if company_nit:
        settings = db_service.get_company_settings(db, company_nit)
        if settings and settings.tasa_renta:
            tasa_renta = Decimal(str(settings.tasa_renta))

    # Try regulatory tarifa table first (supports regime / actividad / surcharges)
    if settings is not None:
        regimen = getattr(settings, "regimen_tributario", None) or "ordinario"
        actividad = getattr(settings, "actividad_economica", None) or "general"
        tarifa_info = db_service.get_tarifa_renta(db, regimen, actividad, year)
        if tarifa_info is not None:
            tasa_renta = Decimal(str(tarifa_info["tarifa_efectiva"]))

    result = calc_period_renta_provision(
        db_session=db,
        nit_receptor=company_nit or "",
        period_start=start,
        period_end=end,
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
        msg = str(e)
        if "F2516" in msg or "requiere F2516" in msg or "Conciliación Fiscal" in msg:
            error_code = "F2516_REQUIRED"
        elif "CompanySettings not found" in msg:
            error_code = "COMPANY_SETTINGS_MISSING"
        elif "Unsupported form_type" in msg:
            error_code = "UNSUPPORTED_FORM_TYPE"
        else:
            error_code = "GENERATION_FAILED"
        raise HTTPException(
            status_code=400,
            detail={"error_code": error_code, "message": msg},
        )

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
    # Block edits on non-draft status
    _draft_for_lock = get_draft(db, draft_id)
    if _draft_for_lock and _draft_for_lock.status != "draft":
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "DRAFT_LOCKED",
                "message": f"El borrador está en estado {_draft_for_lock.status}. Use reopen para editarlo.",
            },
        )
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


def _draft_to_dict(draft: Any) -> Dict[str, Any]:
    """Convert a TaxDeclarationDraft ORM object to API response dict."""
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
        "reviewed_by": draft.reviewed_by,
        "reviewed_at": draft.reviewed_at.isoformat() if draft.reviewed_at else None,
        "filed_by": draft.filed_by,
        "filed_at": draft.filed_at.isoformat() if draft.filed_at else None,
        "dian_acknowledgment": draft.dian_acknowledgment,
        "reopened_by": draft.reopened_by,
        "reopened_at": draft.reopened_at.isoformat() if draft.reopened_at else None,
        "reopen_reason": draft.reopen_reason,
    }


@router.post(
    "/declarations/{draft_id}/review",
    summary="Mark declaration draft as reviewed",
)
def api_review_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Transition draft → reviewed.

    Requires all fields_json entries to have requires_review == False (or absent).
    """
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    if draft.status != "draft":
        raise HTTPException(
            status_code=400,
            detail="Solo borradores pueden marcarse como revisados",
        )

    fields = draft.fields_json or []
    pending = [f for f in fields if f.get("requires_review") is True]
    if pending:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "FIELDS_PENDING_REVIEW",
                "message": f"Hay {len(pending)} campos que requieren revisión",
                "count": len(pending),
            },
        )

    draft.status = "reviewed"
    draft.reviewed_by = current_user.email
    draft.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)
    return _draft_to_dict(draft)


@router.post(
    "/declarations/{draft_id}/file",
    summary="Mark reviewed declaration as filed with DIAN",
)
def api_file_draft(
    draft_id: str,
    body: FileDraftRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Transition reviewed → filed. Optionally stores the DIAN radicado (MUISCA)."""
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    if draft.status != "reviewed":
        raise HTTPException(
            status_code=400,
            detail="Solo declaraciones revisadas pueden marcarse como presentadas",
        )

    draft.status = "filed"
    draft.filed_by = current_user.email
    draft.filed_at = datetime.utcnow()
    if body.dian_acknowledgment is not None:
        draft.dian_acknowledgment = body.dian_acknowledgment
    db.commit()
    db.refresh(draft)
    return _draft_to_dict(draft)


@router.post(
    "/declarations/{draft_id}/reopen",
    summary="Reopen a reviewed or filed declaration for editing",
)
def api_reopen_draft(
    draft_id: str,
    body: ReopenDraftRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Move declaration backward in the workflow:
    - filed → reviewed (clears filed_* fields)
    - reviewed → draft (clears reviewed_* fields)
    - draft → 400 (already editable)

    `reason` is required (min 5 chars).
    """
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Draft not found: {draft_id}")

    if draft.status == "draft":
        raise HTTPException(
            status_code=400,
            detail="Borrador ya está en estado editable",
        )

    now = datetime.utcnow()
    draft.reopened_at = now
    draft.reopened_by = current_user.email
    draft.reopen_reason = body.reason

    if draft.status == "filed":
        draft.status = "reviewed"
        draft.filed_at = None
        draft.filed_by = None
        draft.dian_acknowledgment = None
    else:  # reviewed
        draft.status = "draft"
        draft.reviewed_at = None
        draft.reviewed_by = None

    db.commit()
    db.refresh(draft)
    return _draft_to_dict(draft)


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


# ─── Admin: UVT & Base Mínima constants ──────────────────────────


@router.get("/constants", response_model=TaxConstantsResponse)
async def get_tax_constants(
    year: int = Query(..., ge=2000, le=2100, description="Fiscal year, e.g. 2026"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaxConstantsResponse:
    """Return UVT value and base mínima thresholds stored in DB for a given year."""
    data = db_service.list_tax_constants(db, year)
    return TaxConstantsResponse(
        uvt=data["uvt"],
        base_minima=data["base_minima"],
    )


@router.put("/constants/uvt", response_model=dict)
async def upsert_uvt_value(
    body: UvtUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Insert or update UVT value for a given year. Requires authentication."""
    row = db_service.upsert_uvt(
        db,
        year=body.year,
        value=Decimal(str(body.value)),
        referencia_normativa=body.referencia_normativa,
    )
    return {
        "year": row.year,
        "value": str(row.value),
        "referencia_normativa": row.referencia_normativa,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/constants/base-minima", response_model=dict)
async def upsert_base_minima(
    body: BaseMinimaUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Insert or update base mínima UVT units for a given concepto+year. Requires authentication."""
    if body.concepto not in VALID_CONCEPTO_VALUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"concepto '{body.concepto}' no válido. "
                f"Valores permitidos: {sorted(VALID_CONCEPTO_VALUES)}"
            ),
        )
    row = db_service.upsert_base_minima(
        db,
        concepto=body.concepto,
        uvt_units=Decimal(str(body.uvt_units)),
        year=body.year,
    )
    return {
        "concepto": row.concepto,
        "uvt_units": str(row.uvt_units),
        "year": row.year,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Pérdidas fiscales acumuladas (Art. 147 ET)
# ---------------------------------------------------------------------------


@router.get(
    "/perdidas-acumuladas",
    response_model=list[PerdidaFiscalResponse],
    summary="Listar pérdidas fiscales acumuladas",
)
async def list_perdidas_acumuladas(
    nit: str = Query(..., description="Company NIT"),
    year: Optional[int] = Query(
        None,
        description="Si se envía, filtra pérdidas disponibles previas a este año",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PerdidaFiscalResponse]:
    """
    List all fiscal loss records for a company.
    If `year` is provided, returns only losses with monto_pendiente > 0 from prior years.
    """
    from app.models.database import PerdidaFiscalAcumulada

    try:
        normalized_nit = normalize_nit(nit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid NIT: {exc}") from exc

    if year is not None:
        rows = db_service.get_perdidas_disponibles(db, normalized_nit, year)
    else:
        rows = (
            db.query(PerdidaFiscalAcumulada)
            .filter(PerdidaFiscalAcumulada.company_nit == normalized_nit)
            .order_by(PerdidaFiscalAcumulada.year.asc())
            .all()
        )
    return [
        PerdidaFiscalResponse(
            id=r.id,
            company_nit=r.company_nit,
            year=r.year,
            monto_perdida=str(r.monto_perdida),
            monto_compensado=str(r.monto_compensado),
            monto_pendiente=str(r.monto_pendiente),
            decreto=r.decreto,
            notas=r.notas,
        )
        for r in rows
    ]


@router.post(
    "/perdidas-acumuladas",
    response_model=PerdidaFiscalResponse,
    summary="Crear o actualizar pérdida fiscal acumulada",
    status_code=201,
)
async def upsert_perdida_acumulada(
    body: PerdidaFiscalUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PerdidaFiscalResponse:
    """Insert or update a fiscal loss record for the given company and year."""
    try:
        normalized_nit = normalize_nit(body.company_nit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid NIT: {exc}") from exc

    row = db_service.upsert_perdida(
        db,
        company_nit=normalized_nit,
        year=body.year,
        monto_perdida=Decimal(str(body.monto_perdida)),
        decreto=body.decreto,
        notas=body.notas,
    )
    return PerdidaFiscalResponse(
        id=row.id,
        company_nit=row.company_nit,
        year=row.year,
        monto_perdida=str(row.monto_perdida),
        monto_compensado=str(row.monto_compensado),
        monto_pendiente=str(row.monto_pendiente),
        decreto=row.decreto,
        notas=row.notas,
    )


@router.delete(
    "/perdidas-acumuladas/{perdida_id}",
    status_code=204,
    summary="Eliminar pérdida fiscal acumulada",
)
async def delete_perdida_acumulada(
    perdida_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Hard delete a fiscal loss record by ID."""
    from app.models.database import PerdidaFiscalAcumulada

    row = (
        db.query(PerdidaFiscalAcumulada)
        .filter(PerdidaFiscalAcumulada.id == perdida_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Pérdida fiscal {perdida_id} no encontrada"
        )
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# TarifaRenta endpoints — regulatory income-tax rate table
# ---------------------------------------------------------------------------


@router.get(
    "/tarifas-renta",
    response_model=list[TarifaRentaResponse],
    summary="Listar tarifas de renta PJ por régimen",
)
async def list_tarifas_renta(
    year: Optional[int] = Query(
        None,
        description="Si se envía, filtra sólo las tarifas vigentes para ese año fiscal",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TarifaRentaResponse]:
    """
    List all rows in tarifas_renta. If `year` is provided, returns only rows where
    year_from <= year <= year_to (or year_to IS NULL).
    """
    rows = db_service.list_tarifas_renta(db, year=year)
    return [TarifaRentaResponse(**r) for r in rows]


@router.post(
    "/tarifas-renta",
    response_model=TarifaRentaResponse,
    status_code=201,
    summary="Crear o actualizar tarifa de renta PJ",
)
async def upsert_tarifa_renta(
    body: TarifaRentaUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TarifaRentaResponse:
    """Insert or update a tarifa_renta row keyed by (regimen, actividad, year_from)."""
    from decimal import Decimal

    row = db_service.upsert_tarifa_renta(
        db,
        regimen=body.regimen,
        actividad=body.actividad,
        tarifa_base=Decimal(str(body.tarifa_base)),
        sobretasa=Decimal(str(body.sobretasa)),
        year_from=body.year_from,
        year_to=body.year_to,
        base_legal=body.base_legal,
        notas=body.notas,
    )
    tarifa_efectiva = float(Decimal(str(row.tarifa_base)) + Decimal(str(row.sobretasa)))
    return TarifaRentaResponse(
        id=row.id,
        regimen=row.regimen,
        actividad=row.actividad,
        tarifa_base=float(row.tarifa_base),
        sobretasa=float(row.sobretasa),
        tarifa_efectiva=tarifa_efectiva,
        year_from=row.year_from,
        year_to=row.year_to,
        base_legal=row.base_legal,
        notas=row.notas,
    )


@router.delete(
    "/tarifas-renta/{tarifa_id}",
    status_code=204,
    summary="Eliminar tarifa de renta PJ",
)
async def delete_tarifa_renta(
    tarifa_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Hard delete a tarifa_renta row by ID."""
    from app.models.database import TarifaRenta

    row = db.query(TarifaRenta).filter(TarifaRenta.id == tarifa_id).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Tarifa de renta {tarifa_id} no encontrada"
        )
    db.delete(row)
    db.commit()
