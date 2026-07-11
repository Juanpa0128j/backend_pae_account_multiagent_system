import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
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
from app.core.limiter import limiter
from app.core.database import get_db
from app.models.agent_outputs import IVAOutput, WithholdingsOutput
from app.models.schemas import (
    AjusteFiscalResponse,
    AjusteFiscalUpsertRequest,
    BaseMinimaUpsertRequest,
    FileDraftRequest,
    ICADeclaracionOutput,
    PerdidaFiscalResponse,
    PerdidaFiscalUpsertRequest,
    PreflightResponse,
    RentaProvisionOutput,
    ReopenDraftRequest,
    TarifaRentaResponse,
    TarifaRentaUpsertRequest,
    TaxConceptResponse,
    TaxConceptUpsertRequest,
    TaxConstantsResponse,
    UvtUpsertRequest,
    VALID_CONCEPTO_VALUES,
    ReteicaTarifaResponse,
    ReteicaTarifaUpsertRequest,
)
from app.services import db_service, via_b_service
from app.services.nit_utils import normalize_nit

router = APIRouter()


def _resolve_period(
    period_start: Optional[date],
    period_end: Optional[date],
) -> Tuple[date, date]:
    """Return (start, end). Both None â†’ current month. Partial â†’ 400."""
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
                status_code=422, detail=f"El NIT de la empresa no es vÃ¡lido: {nit_err}"
            )

    result = invoke_reporting_pipeline(
        report_type=report_type,
        report_params=params,
        company_nit=normalized_company_nit,
    )
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result.get("report", {})


def _normalize_or_422(company_nit: Optional[str]) -> Optional[str]:
    """Normalize a NIT or raise HTTP 422."""
    if not company_nit:
        return None
    try:
        return normalize_nit(company_nit)
    except ValueError as e:
        raise HTTPException(
            status_code=422, detail=f"El NIT de la empresa no es vÃ¡lido: {e}"
        )


def _is_via_b(db: Session, company_nit: Optional[str]) -> bool:
    """Return True if the company is locked to the work_with_existing pathway.

    Used to branch the tax endpoints away from the journal-entry-based pipeline
    (which yields zeros for VÃ­a B) to ``via_b_service`` which derives figures
    from uploaded balance / estado de resultados saldos.
    """
    if not company_nit:
        return False
    try:
        return (
            db_service.get_company_locked_pathway(db, company_nit)
            == "work_with_existing"
        )
    except Exception:
        return False


def _empty_referencias(
    statement_kind: str, end_date: date, available_periods: List[str]
) -> List[str]:
    """Compose the explanatory references shown when no data was derivable.

    Distinguishes "you uploaded nothing yet" from "you picked a period that
    isn't in your uploads" â€” the second one lists the periods that do exist
    so the user can switch the PeriodSelector to a valid month.
    """
    if not available_periods:
        return [
            f"No se encontrÃ³ un {statement_kind} cargado para esta empresa (VÃ­a B)."
        ]
    available_str = ", ".join(available_periods)
    return [
        f"No hay {statement_kind} para el perÃ­odo {end_date.isoformat()} (VÃ­a B).",
        f"PerÃ­odos disponibles: {available_str}.",
    ]


def _empty_iva(
    company_nit: Optional[str], end_date: date, available_periods: List[str]
) -> dict:
    """Zero-state IVA payload for a VÃ­a B company with no matching balance."""
    return {
        "report_type": "iva_report",
        "source": "via_b",
        "period_start": None,
        "period_end": end_date.isoformat(),
        "company_nit": company_nit,
        "generated_at": datetime.utcnow().isoformat(),
        "iva_generado": 0.0,
        "iva_descontable": 0.0,
        "iva_a_pagar": 0.0,
        "iva_status": "saldo_cero",
        "referencias": _empty_referencias(
            "balance general", end_date, available_periods
        ),
    }


def _empty_withholdings(
    company_nit: Optional[str], end_date: date, available_periods: List[str]
) -> dict:
    """Zero-state retenciones payload for a VÃ­a B company with no match."""
    return {
        "report_type": "withholdings_report",
        "source": "via_b",
        "period_start": None,
        "period_end": end_date.isoformat(),
        "company_nit": company_nit,
        "generated_at": datetime.utcnow().isoformat(),
        "retencion_en_la_fuente": 0.0,
        "retencion_ica": 0.0,
        "total_retenciones": 0.0,
        "referencias": _empty_referencias(
            "balance general", end_date, available_periods
        ),
    }


def _empty_ica(end_date: date, available_periods: List[str]) -> dict:
    """Zero-state ICA payload for a VÃ­a B company with no matching E.R."""
    return {
        "report_type": "ica_declaracion",
        "source": "via_b",
        "period_start": None,
        "period_end": end_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "ingresos_brutos": 0.0,
        "tasa_ica": float(TASA_ICA_DEFAULT),
        "ica_a_pagar": 0.0,
        "cuenta_gasto_puc": "540101",
        "cuenta_pasivo_puc": "2368",
        "referencias": _empty_referencias(
            "estado de resultados", end_date, available_periods
        ),
    }


def _empty_renta(end_date: date, available_periods: List[str]) -> dict:
    """Zero-state renta payload for a VÃ­a B company with no matching E.R."""
    return {
        "report_type": "renta_provision",
        "source": "via_b",
        "period_start": None,
        "period_end": end_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "utilidad_antes_impuestos": 0.0,
        "tasa_renta": float(TASA_RENTA),
        "provision_renta": 0.0,
        "cuenta_gasto_puc": "540502",
        "cuenta_pasivo_puc": "240405",
        "referencias": _empty_referencias(
            "estado de resultados", end_date, available_periods
        ),
    }


@router.get("/iva", response_model=IVAOutput)
@limiter.limit("60/minute")
def get_iva_report(
    request: Request,
    period_start: Optional[date] = Query(
        None,
        description="Inicio del perÃ­odo YYYY-MM-DD (default: primer dÃ­a del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del perÃ­odo YYYY-MM-DD (default: Ãºltimo dÃ­a del mes actual)",
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Reporte IVA.
    Computes IVA generated (account 240808) vs. IVA deductible (account 240802)
    and returns the net IVA payable with applicable legal references.

    For VÃ­a B (work_with_existing) companies the figures are derived from the
    saldos of cuentas 2408* in the uploaded balance general instead of journal
    entries; the response carries ``source: "via_b"`` so the frontend can
    render a context badge.
    """
    start, end = _resolve_period(period_start, period_end)
    nit = _normalize_or_422(company_nit)
    if _is_via_b(db, nit):
        # Pass the user's raw period_end (may be None) so VÃ­a B falls back to
        # the latest balance when no explicit period was requested. Using the
        # resolved ``end`` would force an exact-month match against today's
        # month and miss the latest upload.
        payload = via_b_service.get_iva_report(db, nit, period_end=period_end)
        if payload is not None:
            return payload
        available = (
            via_b_service.list_periods(db, nit, "balance_general") if nit else []
        )
        return _empty_iva(nit, end, available)
    return _run_report("iva", _build_params(start, end), company_nit)


@router.get("/withholdings", response_model=WithholdingsOutput)
@limiter.limit("60/minute")
def get_withholdings_report(
    request: Request,
    period_start: Optional[date] = Query(
        None,
        description="Inicio del perÃ­odo YYYY-MM-DD (default: primer dÃ­a del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del perÃ­odo YYYY-MM-DD (default: Ãºltimo dÃ­a del mes actual)",
    ),
    company_nit: Optional[str] = Query(None, description="Optional company NIT filter"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Reporte Retenciones.
    Returns Retefuente (account 2365) and ReteICA (account 2368) balances
    with applicable legal references.
    Optionally includes LLM-powered analysis when include_analysis=true.

    For VÃ­a B companies the figures are derived from the saldos of cuentas
    2365/2368 in the uploaded balance general.
    """
    start, end = _resolve_period(period_start, period_end)
    nit = _normalize_or_422(company_nit)
    if _is_via_b(db, nit):
        payload = via_b_service.get_withholdings_report(db, nit, period_end=period_end)
        if payload is not None:
            return payload
        available = (
            via_b_service.list_periods(db, nit, "balance_general") if nit else []
        )
        return _empty_withholdings(nit, end, available)
    return _run_report("withholdings", _build_params(start, end), company_nit)


@router.get("/ica", response_model=ICADeclaracionOutput)
@limiter.limit("60/minute")
def get_ica_declaration(
    request: Request,
    period_start: Optional[date] = Query(
        None,
        description="Inicio del perÃ­odo YYYY-MM-DD (default: primer dÃ­a del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del perÃ­odo YYYY-MM-DD (default: Ãºltimo dÃ­a del mes actual)",
    ),
    company_nit: Optional[str] = Query(
        None, description="Company NIT (nit_receptor) to filter by"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    DeclaraciÃ³n ICA â€” Impuesto de Industria y Comercio.
    Aggregates PUC 4xxx credit entries for the period, filtered by company NIT
    (nit_receptor on transactions_posted), and applies the company's tasa_ica
    (or national default 6.9â€°).
    Ref: Ley 14/1983, Decreto 1333/1986.

    For VÃ­a B companies, ``ingresos_brutos`` comes from the uploaded estado de
    resultados instead of summing journal entries.
    """
    start, end = _resolve_period(period_start, period_end)
    nit = _normalize_or_422(company_nit)

    if _is_via_b(db, nit):
        tasa_ica_vb: Optional[Decimal] = None
        if nit:
            settings = db_service.get_company_settings(db, nit)
            if settings and settings.tasa_ica:
                tasa_ica_vb = Decimal(str(settings.tasa_ica))
        payload = via_b_service.get_ica_report(
            db, nit, period_end=period_end, tasa_ica=tasa_ica_vb
        )
        if payload is None:
            available = (
                via_b_service.list_periods(db, nit, "estado_resultados") if nit else []
            )
            return ICADeclaracionOutput(**_empty_ica(end, available))
        # Honor the per-company cuenta_pasivo override even on VÃ­a B
        if nit:
            settings = db_service.get_company_settings(db, nit)
            if settings and settings.cuenta_ica_propio:
                payload["cuenta_pasivo_puc"] = settings.cuenta_ica_propio
        return ICADeclaracionOutput(**payload)

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
            "Ley 14 de 1983 â€” Impuesto de Industria y Comercio",
            "Decreto 1333 de 1986 â€” CÃ³digo de RÃ©gimen Municipal",
            "Art. 342 Ley 1955/2019 â€” territorialidad ICA",
        ],
    )


@router.get("/renta-provision", response_model=RentaProvisionOutput)
@limiter.limit("60/minute")
def get_renta_provision(
    request: Request,
    period_start: Optional[date] = Query(
        None,
        description="Inicio del perÃ­odo YYYY-MM-DD (default: primer dÃ­a del mes actual)",
    ),
    period_end: Optional[date] = Query(
        None,
        description="Fin del perÃ­odo YYYY-MM-DD (default: Ãºltimo dÃ­a del mes actual)",
    ),
    company_nit: Optional[str] = Query(None, description="Company NIT to filter by"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    ProvisiÃ³n Impuesto de Renta â€” tarifa general 35% (Art. 240 ET, Ley 2277/2022).
    Aggregates income (4xxx credits), costs (6xxx debits), and expenses (5xxx debits)
    from journal_entry_lines to compute net income and the corresponding tax provision.

    For VÃ­a B companies, ``utilidad_antes_impuestos`` comes from the uploaded
    estado de resultados instead of summing journal entries.
    """
    start, end = _resolve_period(period_start, period_end)
    year = end.year
    nit = _normalize_or_422(company_nit)

    if _is_via_b(db, nit):
        tasa_renta_vb: Optional[Decimal] = None
        if nit:
            settings_vb = db_service.get_company_settings(db, nit)
            if settings_vb and settings_vb.tasa_renta:
                tasa_renta_vb = Decimal(str(settings_vb.tasa_renta))
        payload = via_b_service.get_renta_provision_report(
            db, nit, period_end=period_end, tasa_renta=tasa_renta_vb
        )
        if payload is None:
            available = (
                via_b_service.list_periods(db, nit, "estado_resultados") if nit else []
            )
            return RentaProvisionOutput(**_empty_renta(end, available))
        return RentaProvisionOutput(**payload)

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


@router.get(
    "/declarations/preflight",
    response_model=PreflightResponse,
    summary="Pre-flight validation before generating a DIAN declaration draft",
)
@limiter.limit("60/minute")
def api_declarations_preflight(
    request: Request,
    company_nit: str = Query(..., min_length=1),
    form_type: str = Query(..., description="F300 | F350 | F110 | F2516 | ICA"),
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PreflightResponse:
    """
    Validate that all prerequisites are in place to generate a declaration
    draft for the given form_type and period. Returns a structured list of
    checks (blockers / warnings / info) so the UI can guide the accountant
    before they click "Generar borrador".
    """
    from app.services.preflight_service import run_preflight

    if period_end < period_start:
        raise HTTPException(
            status_code=400,
            detail="period_end no puede ser anterior a period_start",
        )
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as nit_err:
        raise HTTPException(
            status_code=422, detail=f"El NIT de la empresa no es vÃ¡lido: {nit_err}"
        )

    try:
        result = run_preflight(
            db=db,
            company_nit=normalized_nit,
            form_type=form_type,
            period_start=period_start,
            period_end=period_end,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "INVALID_FORM_TYPE", "message": str(e)},
        )
    return PreflightResponse(**result)


@router.post(
    "/declarations/generate", summary="Generate pre-filled DIAN declaration draft"
)
@limiter.limit("30/minute")
def api_generate_draft(
    request: Request,
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
        if "F2516" in msg or "requiere F2516" in msg or "ConciliaciÃ³n Fiscal" in msg:
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
@limiter.limit("60/minute")
def api_get_draft(
    request: Request,
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve a draft by ID including all pre-filled renglones and warnings."""
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(
            status_code=404, detail=f"Borrador {draft_id} no encontrado."
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
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


@router.patch(
    "/declarations/{draft_id}/fields", summary="Update a requires_review field"
)
@limiter.limit("30/minute")
def api_update_draft_field(
    request: Request,
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
                "message": f"El borrador estÃ¡ en estado {_draft_for_lock.status}. Use reopen para editarlo.",
            },
        )
    try:
        draft = update_draft_field(db, draft_id, body.renglon, body.value)
    except FieldNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FieldNotEditableError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not draft:
        raise HTTPException(
            status_code=404, detail=f"Borrador {draft_id} no encontrado."
        )

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
@limiter.limit("30/minute")
def api_review_draft(
    request: Request,
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Transition draft â†’ reviewed.

    Requires all fields_json entries to have requires_review == False (or absent).
    """
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(
            status_code=404, detail=f"Borrador {draft_id} no encontrado."
        )

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
                "message": f"Hay {len(pending)} campos que requieren revisiÃ³n",
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
@limiter.limit("30/minute")
def api_file_draft(
    request: Request,
    draft_id: str,
    body: FileDraftRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Transition reviewed â†’ filed. Optionally stores the DIAN radicado (MUISCA)."""
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(
            status_code=404, detail=f"Borrador {draft_id} no encontrado."
        )

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
@limiter.limit("30/minute")
def api_reopen_draft(
    request: Request,
    draft_id: str,
    body: ReopenDraftRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Move declaration backward in the workflow:
    - filed â†’ reviewed (clears filed_* fields)
    - reviewed â†’ draft (clears reviewed_* fields)
    - draft â†’ 400 (already editable)

    `reason` is required (min 5 chars).
    """
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(
            status_code=404, detail=f"Borrador {draft_id} no encontrado."
        )

    if draft.status == "draft":
        raise HTTPException(
            status_code=400,
            detail="Borrador ya estÃ¡ en estado editable",
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
@limiter.limit("60/minute")
def api_tax_calendar(
    request: Request,
    nit: str = Query(..., description="Company NIT (without DV)"),
    year: int = Query(2026, description="Tax year"),
    iva_regime: str = Query("bimestral", description="bimestral | cuatrimestral"),
    alert_days: int = Query(30, description="Days-until threshold for alert flag"),
    ica_periodicidad: Optional[str] = Query(
        None,
        description=(
            "ICA municipal opcional: None | anual | bimestral. Fechas ESTIMADAS "
            "(confirme calendario municipal)."
        ),
    ),
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
            detail=f"AÃ±o {year} no soportado. AÃ±os disponibles: {sorted(SUPPORTED_YEARS)}",
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
            ica_periodicidad=ica_periodicidad,
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
@limiter.limit("30/minute")
def api_generate_f220(
    request: Request,
    company_nit: str = Query(..., description="Company NIT (retenedor)"),
    year: int = Query(..., description="Tax year (e.g. 2025)"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate F220 Certificado de RetenciÃ³n en la Fuente for every tercero
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
    summary="Generate DIAN exÃ³gena (medios magnÃ©ticos) data",
)
@limiter.limit("60/minute")
def api_exogena(
    request: Request,
    formato: str,
    company_nit: str = Query(..., description="Reporting company NIT"),
    year: int = Query(..., description="Tax year (e.g. 2025)"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate normalized exÃ³gena data for DIAN medios magnÃ©ticos.

    Supported formats:
      - 1001: Pagos o abonos en cuenta y retenciones practicadas
      - 2276: Ingresos recibidos por personas naturales/jurÃ­dicas

    All NIT values are digit-only; names are UPPERCASE with accents removed
    and Ã‘â†’N per DIAN strict normalization (ResoluciÃ³n 000162/2023).
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


# â”€â”€â”€ Admin: ReteICA Municipal Tarifas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.get("/reteica-tarifas", response_model=list[ReteicaTarifaResponse])
@limiter.limit("60/minute")
def list_reteica_tarifas_endpoint(
    request: Request,
    municipio: Optional[str] = Query(
        None, description="Filter by city name (lowercase)"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReteicaTarifaResponse]:
    """List ReteICA tariff rows. Optional filter by municipio."""
    rows = db_service.list_reteica_tarifas(db, municipio=municipio)
    return [ReteicaTarifaResponse(**r) for r in rows]


@router.put("/reteica-tarifas", response_model=ReteicaTarifaResponse)
@limiter.limit("30/minute")
def upsert_reteica_tarifa_endpoint(
    request: Request,
    body: ReteicaTarifaUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReteicaTarifaResponse:
    """Insert or update a ReteICA tariff row keyed by (municipio, ciiu_seccion)."""
    row = db_service.upsert_reteica_tarifa(
        db,
        municipio=body.municipio,
        ciiu_seccion=body.ciiu_seccion,
        tasa=Decimal(str(body.tasa)),
        fuente=body.fuente,
        base_minima_uvt=(
            Decimal(str(body.base_minima_uvt))
            if body.base_minima_uvt is not None
            else None
        ),
    )
    return ReteicaTarifaResponse(
        id=row.id,
        municipio=row.municipio,
        ciiu_seccion=row.ciiu_seccion,
        tasa=float(row.tasa),
        fuente=row.fuente,
        base_minima_uvt=(
            float(row.base_minima_uvt) if row.base_minima_uvt is not None else None
        ),
    )


@router.delete("/reteica-tarifas/{row_id}", status_code=204)
@limiter.limit("30/minute")
def delete_reteica_tarifa_endpoint(
    request: Request,
    row_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a ReteICA tariff row by id."""
    deleted = db_service.delete_reteica_tarifa(db, row_id=row_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"ReteicaTarifa id={row_id} not found."
        )
    return Response(status_code=204)


# â”€â”€â”€ Admin: UVT & Base MÃ­nima constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@router.get("/constants", response_model=TaxConstantsResponse)
@limiter.limit("60/minute")
def get_tax_constants(
    request: Request,
    year: int = Query(..., ge=2000, le=2100, description="Fiscal year, e.g. 2026"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaxConstantsResponse:
    """Return UVT value and base mÃ­nima thresholds stored in DB for a given year."""
    data = db_service.list_tax_constants(db, year)
    return TaxConstantsResponse(
        uvt=data["uvt"],
        base_minima=data["base_minima"],
        tarifas_renta=data["tarifas_renta"],
        tax_concepts=data["tax_concepts"],
    )


@router.put("/constants/uvt", response_model=dict)
@limiter.limit("30/minute")
def upsert_uvt_value(
    request: Request,
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
@limiter.limit("30/minute")
def upsert_base_minima(
    request: Request,
    body: BaseMinimaUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Insert or update base mÃ­nima UVT units for a given concepto+year. Requires authentication."""
    if body.concepto not in VALID_CONCEPTO_VALUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"concepto '{body.concepto}' no vÃ¡lido. "
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
# PÃ©rdidas fiscales acumuladas (Art. 147 ET)
# ---------------------------------------------------------------------------


@router.get(
    "/perdidas-acumuladas",
    response_model=list[PerdidaFiscalResponse],
    summary="Listar pÃ©rdidas fiscales acumuladas",
)
@limiter.limit("60/minute")
def list_perdidas_acumuladas(
    request: Request,
    nit: str = Query(..., description="Company NIT"),
    year: Optional[int] = Query(
        None,
        description="Si se envÃ­a, filtra pÃ©rdidas disponibles previas a este aÃ±o",
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
        raise HTTPException(
            status_code=422, detail=f"El NIT ingresado no es vÃ¡lido: {exc}"
        ) from exc

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
    summary="Crear o actualizar pÃ©rdida fiscal acumulada",
    status_code=201,
)
@limiter.limit("30/minute")
def upsert_perdida_acumulada(
    request: Request,
    body: PerdidaFiscalUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PerdidaFiscalResponse:
    """Insert or update a fiscal loss record for the given company and year."""
    try:
        normalized_nit = normalize_nit(body.company_nit)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"El NIT ingresado no es vÃ¡lido: {exc}"
        ) from exc

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
    summary="Eliminar pÃ©rdida fiscal acumulada",
)
@limiter.limit("30/minute")
def delete_perdida_acumulada(
    request: Request,
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
            status_code=404, detail=f"PÃ©rdida fiscal {perdida_id} no encontrada"
        )
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# TarifaRenta endpoints â€” regulatory income-tax rate table
# ---------------------------------------------------------------------------


@router.get(
    "/tarifas-renta",
    response_model=list[TarifaRentaResponse],
    summary="Listar tarifas de renta PJ por rÃ©gimen",
)
@limiter.limit("60/minute")
def list_tarifas_renta(
    request: Request,
    year: Optional[int] = Query(
        None,
        description="Si se envÃ­a, filtra sÃ³lo las tarifas vigentes para ese aÃ±o fiscal",
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
@limiter.limit("30/minute")
def upsert_tarifa_renta(
    request: Request,
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
@limiter.limit("30/minute")
def delete_tarifa_renta(
    request: Request,
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


# ---------------------------------------------------------------------------
# TaxConcept endpoints â€” F350 retenciÃ³n catalog (Res. DIAN 000031/2024)
# ---------------------------------------------------------------------------


@router.get(
    "/concepts",
    response_model=list[TaxConceptResponse],
    summary="Listar conceptos de retenciÃ³n F350",
)
@limiter.limit("60/minute")
def list_tax_concepts_endpoint(
    request: Request,
    activo: Optional[bool] = Query(
        None,
        description="True = solo activos; False = solo inactivos; null (default) = todos",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaxConceptResponse]:
    """List tax_concepts rows. No activo filter by default (returns all)."""
    rows = db_service.list_tax_concepts(db, activo=activo)
    return [TaxConceptResponse(**r) for r in rows]


@router.put(
    "/concepts",
    response_model=TaxConceptResponse,
    status_code=200,
    summary="Crear o actualizar concepto de retenciÃ³n",
)
@limiter.limit("30/minute")
def upsert_tax_concept_endpoint(
    request: Request,
    body: TaxConceptUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaxConceptResponse:
    """Insert or update a tax_concepts row keyed by code."""
    row = db_service.upsert_tax_concept(
        db,
        code=body.code,
        label=body.label,
        renglon_350=body.renglon_350,
        aplica_a=body.aplica_a,
        categoria=body.categoria,
        tarifa_default=(
            Decimal(str(body.tarifa_default))
            if body.tarifa_default is not None
            else None
        ),
        base_minima_uvt=(
            Decimal(str(body.base_minima_uvt))
            if body.base_minima_uvt is not None
            else None
        ),
        art_referencia=body.art_referencia,
        activo=body.activo,
    )
    return TaxConceptResponse(
        code=row.code,
        label=row.label,
        renglon_350=row.renglon_350,
        aplica_a=row.aplica_a,
        tarifa_default=(
            float(row.tarifa_default) if row.tarifa_default is not None else None
        ),
        base_minima_uvt=(
            float(row.base_minima_uvt) if row.base_minima_uvt is not None else None
        ),
        categoria=row.categoria,
        art_referencia=row.art_referencia,
        activo=bool(row.activo),
    )


@router.delete(
    "/concepts/{code}",
    status_code=204,
    summary="Soft delete (activo=False) concepto de retenciÃ³n",
)
@limiter.limit("30/minute")
def delete_tax_concept_endpoint(
    request: Request,
    code: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Soft delete: marks activo=False so historical data stays queryable."""
    row = db_service.soft_delete_tax_concept(db, code)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Concepto de retenciÃ³n '{code}' no encontrado"
        )


# ---------------------------------------------------------------------------
# AjusteFiscal endpoints â€” F2516 fiscal reconciliation adjustments
# ---------------------------------------------------------------------------


def _ajuste_to_response(row) -> AjusteFiscalResponse:
    return AjusteFiscalResponse(
        id=row.id,
        company_nit=row.company_nit,
        year=row.year,
        seccion=row.seccion,
        concepto=row.concepto,
        valor_contable=float(row.valor_contable),
        valor_fiscal=float(row.valor_fiscal),
        tipo_diferencia=row.tipo_diferencia,
        descripcion=row.descripcion,
    )


@router.get(
    "/ajustes-fiscales",
    response_model=list[AjusteFiscalResponse],
    summary="Listar ajustes fiscales para F2516",
)
@limiter.limit("60/minute")
def list_ajustes_fiscales(
    request: Request,
    company_nit: str = Query(..., description="Company NIT"),
    year: int = Query(..., ge=1990, le=2100),
    seccion: Optional[str] = Query(
        None,
        description="ESF_ACTIVO | ESF_PASIVO | ESF_PATRIMONIO | ERI_INGRESO | ERI_COSTO | ERI_GASTO",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AjusteFiscalResponse]:
    try:
        normalized_nit = normalize_nit(company_nit)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"El NIT ingresado no es vÃ¡lido: {exc}"
        ) from exc

    rows = db_service.list_ajustes_fiscales(db, normalized_nit, year, seccion)
    return [_ajuste_to_response(r) for r in rows]


@router.put(
    "/ajustes-fiscales",
    response_model=AjusteFiscalResponse,
    summary="Crear o actualizar un ajuste fiscal (F2516)",
)
@limiter.limit("30/minute")
def upsert_ajuste_fiscal(
    request: Request,
    body: AjusteFiscalUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AjusteFiscalResponse:
    try:
        normalized_nit = normalize_nit(body.company_nit)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"El NIT ingresado no es vÃ¡lido: {exc}"
        ) from exc

    row = db_service.upsert_ajuste_fiscal(
        db,
        company_nit=normalized_nit,
        year=body.year,
        seccion=body.seccion,
        concepto=body.concepto,
        valor_contable=Decimal(str(body.valor_contable)),
        valor_fiscal=Decimal(str(body.valor_fiscal)),
        tipo_diferencia=body.tipo_diferencia,
        descripcion=body.descripcion,
    )
    return _ajuste_to_response(row)


@router.delete(
    "/ajustes-fiscales/{ajuste_id}",
    status_code=204,
    summary="Eliminar un ajuste fiscal",
)
@limiter.limit("30/minute")
def delete_ajuste_fiscal(
    request: Request,
    ajuste_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    deleted = db_service.delete_ajuste_fiscal(db, ajuste_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Ajuste fiscal {ajuste_id} no encontrado"
        )
