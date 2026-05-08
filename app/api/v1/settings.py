"""
Company settings API — manage per-tenant tax configuration.

Endpoints:
  GET    /api/v1/settings/company/{nit}        — retrieve settings for a company
  PUT    /api/v1/settings/company/{nit}        — create or replace settings (manual rates)
  DELETE /api/v1/settings/company/{nit}        — permanently delete a company
  POST   /api/v1/settings/company/{nit}/setup  — auto-compute rates from city/CIIU/régimen
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.llm_client import get_llm_client
from app.models.schemas import (
    CompanyProfileSetupRequest,
    CompanySettingsRequest,
    CompanySettingsResponse,
)
from app.services import db_service
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Retefuente national rates (Art. 383 / Art. 401 ET) — these are fixed by law
# and do not vary by municipality, so they are always taken from the code.
_TASA_RETEFUENTE_SERVICIOS = (
    0.04  # Retefuente servicios generales, declarantes (DIAN 2026)
)
_TASA_RETEFUENTE_BIENES = 0.03
_TASA_RETEFUENTE_ARRENDAMIENTO = 0.10


@router.get("/company/{nit}", response_model=CompanySettingsResponse)
def get_company_settings(nit: str, db: Session = Depends(get_db)):
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
def list_companies(db: Session = Depends(get_db)):
    """Return all registered companies (used for the frontend company selector)."""
    return db_service.list_companies(db)


@router.delete("/company/{nit}", status_code=204)
def delete_company(nit: str, db: Session = Depends(get_db)):
    """Permanently delete a company and its tax settings."""
    deleted = db_service.delete_company(db, nit)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Company '{nit}' not found.")
    return Response(status_code=204)


@router.put("/company/{nit}", response_model=CompanySettingsResponse)
def upsert_company_settings(
    nit: str,
    body: CompanySettingsRequest,
    db: Session = Depends(get_db),
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
def setup_company_tax_profile(
    nit: str,
    body: CompanyProfileSetupRequest,
    db: Session = Depends(get_db),
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

    # ── Step 3: Persist ───────────────────────────────────────────────────────
    settings_data = {
        "nombre": body.nombre,
        "ciudad": body.ciudad,
        "codigo_ciiu": body.codigo_ciiu,
        "iva_responsable": body.iva_responsable,
        "tasa_retefuente_servicios": _TASA_RETEFUENTE_SERVICIOS,
        "tasa_retefuente_bienes": _TASA_RETEFUENTE_BIENES,
        "tasa_retefuente_arrendamiento": _TASA_RETEFUENTE_ARRENDAMIENTO,
        "tasa_reteica": tasa_reteica,
        "tasa_iva_general": tasa_iva,
        "tasa_ica": tasa_ica,
        "tasa_renta": 0.35,  # Fixed — Art. 240 ET, Ley 2277/2022. Never inferred.
    }
    return db_service.upsert_company_settings(db, nit, settings_data)
