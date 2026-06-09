"""
Company settings API — manage per-tenant tax configuration.

Endpoints:
  GET    /api/v1/settings/company/{nit}        — retrieve settings for a company
  PUT    /api/v1/settings/company/{nit}        — create or replace settings (manual rates)
  DELETE /api/v1/settings/company/{nit}        — permanently delete a company
  POST   /api/v1/settings/company/{nit}/setup  — auto-compute rates from city/CIIU/régimen
"""

import logging
from decimal import Decimal
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.limiter import limiter
from app.core.database import get_db
from app.core.llm_client import get_llm_client
from app.models.database import CompanyPucConfig
from app.models.schemas import (
    CompanyPucEntryResponse,
    CompanyPucToggleRequest,
    CompanyProfileSetupRequest,
    CompanyRateOverrideRequest,
    CompanySettingsRequest,
    CompanySettingsResponse,
    EffectiveRateResponse,
    NationalRateResponse,
    NationalRateUpdateRequest,
)
from app.services import db_service
from app.services.nit_utils import normalize_nit
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Retefuente national rates (Art. 383 / Art. 401 ET) — these are fixed by law
# and do not vary by municipality, so they are always taken from the code.
_TASA_RETEFUENTE_SERVICIOS = (
    0.04  # Retefuente servicios generales, declarantes (DIAN 2026)
)
_TASA_RETEFUENTE_BIENES = 0.025
_TASA_RETEFUENTE_ARRENDAMIENTO = 0.035


@router.get("/company/{nit}", response_model=CompanySettingsResponse)
@limiter.limit("60/minute")
def get_company_settings(
    request: Request,
    nit: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the tax configuration for the given company NIT."""
    row = db_service.get_company_settings(db, nit)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No tax settings found for NIT '{nit}'. "
            "Use PUT to set rates manually or POST /setup to compute them automatically.",
        )
    return row


@router.get("/companies", response_model=list[CompanySettingsResponse])
@limiter.limit("60/minute")
def list_companies(
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return all registered companies (used for the frontend company selector)."""
    return db_service.list_companies(db)


@router.get("/municipios", response_model=list[str])
@limiter.limit("60/minute")
def list_municipios(
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return sorted municipios that have ReteICA tariff data."""
    return db_service.get_municipios(db)


@router.delete("/company/{nit}", status_code=204)
@limiter.limit("30/minute")
def delete_company(
    request: Request,
    nit: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Permanently delete a company and its tax settings."""
    deleted = db_service.delete_company(db, nit)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Company '{nit}' not found.")
    return Response(status_code=204)


@router.put("/company/{nit}", response_model=CompanySettingsResponse)
@limiter.limit("30/minute")
def upsert_company_settings(
    request: Request,
    nit: str,
    body: CompanySettingsRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update the tax configuration for the given company NIT.

    PATCH semantics: only fields explicitly set by the client are persisted.
    Omitted/None fields keep their current DB value. This prevents wiping out
    `nombre`, `ciudad`, `codigo_ciiu`, etc. when a partial update is sent.
    """
    payload = body.model_dump(exclude_unset=True, exclude_none=True)
    if not payload:
        raise HTTPException(
            status_code=400,
            detail="Solicitud vacía: incluya al menos un campo a actualizar.",
        )
    return db_service.upsert_company_settings(db, nit, payload)


@router.post("/company/{nit}/setup", response_model=CompanySettingsResponse)
@limiter.limit("30/minute")
def setup_company_tax_profile(
    request: Request,
    nit: str,
    body: CompanyProfileSetupRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Auto-compute and save the correct Colombian tax rates for a company.

    The user provides their city, CIIU code, and IVA régimen.

    ReteICA lookup order:
      1. reteica_tarifas table (relational, authoritative, seeded from municipal acuerdos)
      2. RAG normativo + Gemini (fallback when city/CIIU not in table)
      3. National default (0.69%) if all else fails

    Retefuente rates are always taken from national law (Art. 383/401 ET) — they
    do not vary by municipality.
    """
    # ── Step 1: ReteICA — relational DB lookup (primary source) ─────────────────
    # reteica_tarifas only stores the ReteICA retention rate. ICA (the underlying
    # activity tax) is tracked separately because municipalities like Bogotá apply
    # a flat ReteICA regardless of the per-activity ICA rate (4.14‰–13.8‰).
    # When no separate ICA source is available we use ReteICA as the best-effort
    # fallback and surface a warning so the user knows the value may need review.
    tasa_reteica = db_service.get_reteica_tarifa(db, body.ciudad, body.codigo_ciiu)
    tasa_ica: float | None = None
    reteica_source = "db"

    if tasa_reteica is None:
        logger.info(
            f"Tax profile setup: No DB entry for ciudad={body.ciudad}, "
            f"ciiu={body.codigo_ciiu} — falling back to RAG+LLM"
        )
        reteica_source = "llm"

        # ── Step 2: RAG + LLM fallback ────────────────────────────────────────
        rag_context = ""
        try:
            rag = get_rag_service()
            queries = [
                f"tasa ReteICA municipio {body.ciudad} impuesto industria comercio ICA",
                f"tarifa ICA actividad CIIU {body.codigo_ciiu}",
            ]
            results = []
            for q in queries:
                results.extend(rag.search_normativo(q, n_results=3))
            rag_context = "\n\n".join(
                f"[{r.metadata.get('source', 'normativa')}]: {r.content}"
                for r in results
            )
        except Exception as rag_err:
            logger.warning(f"Tax profile setup: RAG lookup failed ({rag_err})")

        try:
            llm = get_llm_client()
            rate_lookup = llm.compute_tax_rates_from_profile(
                ciudad=body.ciudad,
                codigo_ciiu=body.codigo_ciiu,
                iva_responsable=body.iva_responsable,
                rag_context=rag_context,
            )
            tasa_reteica = rate_lookup.tasa_reteica
            tasa_ica = rate_lookup.tasa_ica
        except Exception as llm_err:
            logger.warning(
                f"Tax profile setup: LLM rate lookup failed ({llm_err}), "
                "using national default"
            )
            tasa_reteica = 0.0069  # national reference rate
            tasa_ica = 0.0069
            reteica_source = "default"

    if tasa_ica is None:
        # Fell through the DB path which only stores ReteICA. Use it as the best
        # available proxy for ICA, but warn the operator: this is wrong for any
        # municipality where the activity tariff differs from the retention.
        logger.warning(
            "Tax profile setup for NIT %s: no separate ICA source available — "
            "using ReteICA (%s) as ICA proxy. Verify against municipal acuerdo.",
            nit,
            tasa_reteica,
        )
        tasa_ica = tasa_reteica

    tasa_iva = 0.19 if body.iva_responsable else 0.0

    logger.info(
        f"Tax profile setup for NIT {nit}: "
        f"ciudad={body.ciudad}, ciiu={body.codigo_ciiu}, "
        f"reteica={tasa_reteica} (source={reteica_source}), "
        f"iva_responsable={body.iva_responsable}"
    )

    # ── Step 3a: Read national rates from DB (fallback to module constants) ──────
    def _get_rate(code: str, fallback: float) -> float:
        row = db_service.get_national_rate(db, code)
        if row is None:
            logger.warning(
                "setup_company_tax_profile: national_rates DB entry missing for "
                "code=%s — using hardcoded fallback %.4f. "
                "Ensure migration b8c9d0e1f2a3 was applied.",
                code,
                fallback,
            )
            return fallback
        return float(row.value)

    _TASA_RENTA_FALLBACK = 0.35  # Art. 240 ET, Ley 2277/2022
    tasa_retefuente_servicios = _get_rate(
        "retefuente_servicios", _TASA_RETEFUENTE_SERVICIOS
    )
    tasa_retefuente_bienes = _get_rate("retefuente_bienes", _TASA_RETEFUENTE_BIENES)
    tasa_retefuente_arrendamiento = _get_rate(
        "retefuente_arrendamiento", _TASA_RETEFUENTE_ARRENDAMIENTO
    )
    tasa_renta = _get_rate("renta_general", _TASA_RENTA_FALLBACK)

    # ── Step 3b: Persist ──────────────────────────────────────────────────────────
    settings_data = {
        "nombre": body.nombre,
        "ciudad": body.ciudad,
        "codigo_ciiu": body.codigo_ciiu,
        "iva_responsable": body.iva_responsable,
        "tasa_retefuente_servicios": tasa_retefuente_servicios,
        "tasa_retefuente_bienes": tasa_retefuente_bienes,
        "tasa_retefuente_arrendamiento": tasa_retefuente_arrendamiento,
        "tasa_reteica": tasa_reteica,
        "tasa_iva_general": tasa_iva,
        "tasa_ica": tasa_ica,
        "tasa_renta": tasa_renta,
    }
    return db_service.upsert_company_settings(db, nit, settings_data)


# ── National Rates ───────────────────────────────────────────────────────────


@router.get("/national-rates", response_model=list[NationalRateResponse])
@limiter.limit("60/minute")
async def list_national_rates_endpoint(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[NationalRateResponse]:
    """List all configurable national statutory tax rates."""
    rows = db_service.list_national_rates(db)
    return [NationalRateResponse(**r) for r in rows]


@router.put(
    "/national-rates/{code}",
    response_model=NationalRateResponse,
)
@limiter.limit("30/minute")
async def update_national_rate_endpoint(
    request: Request,
    code: str,
    body: NationalRateUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NationalRateResponse:
    """Update a national statutory tax rate by code.

    Valid codes: retefuente_servicios, retefuente_bienes,
    retefuente_arrendamiento, renta_general.
    """
    row = db_service.upsert_national_rate(
        db,
        code=code,
        value=Decimal(str(body.value)),
        descripcion=body.descripcion,
        norma_referencia=body.norma_referencia,
        vigente_desde=date_type.fromisoformat(body.vigente_desde),
    )
    return NationalRateResponse(
        code=row.code,
        value=float(row.value),
        descripcion=row.descripcion,
        norma_referencia=row.norma_referencia,
        vigente_desde=row.vigente_desde.isoformat(),
    )


# ── Company-scoped PUC and Rate Overrides ────────────────────────────────────


@router.get("/company/{nit}/puc", response_model=list[CompanyPucEntryResponse])
@limiter.limit("60/minute")
async def list_company_puc(
    request: Request,
    nit: str,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CompanyPucEntryResponse]:
    """List full PUC catalog with per-company activation status overlay.

    Returns all active global accounts (or all if include_inactive=True) with
    per-company is_active_for_company flag based on company_puc_config.
    If company has no config entry for an account, it defaults to active.
    """
    nit = normalize_nit(nit)
    # Get all global active accounts (or all accounts if include_inactive)
    all_accounts = (
        db_service.get_all_puc_including_inactive(db)
        if include_inactive
        else db_service.get_all_puc(db)
    )
    # Load company configs
    configs = {
        c.cuenta_codigo: c
        for c in db.query(CompanyPucConfig).filter_by(company_nit=nit).all()
    }

    return [
        CompanyPucEntryResponse(
            codigo=a.codigo,
            nombre=a.nombre,
            clase=a.clase,
            naturaleza=a.naturaleza.value,
            activa=a.activa,
            is_active_for_company=(
                configs[a.codigo].is_active if a.codigo in configs else True
            ),
            custom_nombre=(
                configs[a.codigo].custom_nombre if a.codigo in configs else None
            ),
        )
        for a in all_accounts
    ]


@router.put(
    "/company/{nit}/puc/{codigo}",
    response_model=CompanyPucEntryResponse,
)
@limiter.limit("30/minute")
async def toggle_company_puc(
    request: Request,
    nit: str,
    codigo: str,
    body: CompanyPucToggleRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> CompanyPucEntryResponse:
    """Toggle PUC account activation for a specific company.

    Updates company_puc_config to mark an account as active/inactive for
    the given company. Returns the updated account entry with current status.
    """
    nit = normalize_nit(nit)
    # Upsert the config
    db_service.set_company_puc_config(
        db,
        company_nit=nit,
        cuenta_codigo=codigo,
        is_active=body.is_active,
        custom_nombre=body.custom_nombre,
    )
    # Fetch the account to return
    account = db_service.validate_puc_exists(db, codigo)
    if not account:
        raise HTTPException(
            status_code=404,
            detail=f"PUC account {codigo} not found",
        )
    # Get the config we just upserted
    config = (
        db.query(CompanyPucConfig)
        .filter_by(company_nit=nit, cuenta_codigo=codigo)
        .first()
    )
    return CompanyPucEntryResponse(
        codigo=account.codigo,
        nombre=account.nombre,
        clase=account.clase,
        naturaleza=account.naturaleza.value,
        activa=account.activa,
        is_active_for_company=config.is_active if config else True,
        custom_nombre=config.custom_nombre if config else None,
    )


@router.get(
    "/company/{nit}/rates",
    response_model=list[EffectiveRateResponse],
)
@limiter.limit("60/minute")
async def list_company_rates(
    request: Request,
    nit: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[EffectiveRateResponse]:
    """List effective tax rates for a company.

    Returns national rates with company overrides layered on top.
    Each rate includes an 'overridden' flag (True if company has an override).
    """
    nit = normalize_nit(nit)
    rows = db_service.get_effective_rates(db, nit)
    return [EffectiveRateResponse(**r) for r in rows]


@router.put(
    "/company/{nit}/rates/{code}",
    response_model=EffectiveRateResponse,
)
@limiter.limit("30/minute")
async def upsert_company_rate(
    request: Request,
    nit: str,
    code: str,
    body: CompanyRateOverrideRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EffectiveRateResponse:
    """Upsert a company-specific tax rate override.

    Creates or updates a company_rate_override for the given rate code.
    Returns the updated effective rate with overridden flag.
    """
    nit = normalize_nit(nit)
    db_service.upsert_company_rate_override(
        db,
        company_nit=nit,
        rate_code=code,
        value=Decimal(str(body.value)),
        norma_referencia=body.norma_referencia,
        vigente_desde=date_type.fromisoformat(body.vigente_desde),
    )
    # Fetch updated rates and return the specific one
    rows = db_service.get_effective_rates(db, nit)
    rate = next((r for r in rows if r["code"] == code), None)
    if not rate:
        raise HTTPException(
            status_code=404,
            detail=f"Rate code {code} not found",
        )
    return EffectiveRateResponse(**rate)
