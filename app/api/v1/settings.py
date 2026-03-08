"""
Company settings API — manage per-tenant tax configuration.

Endpoints:
  GET  /api/v1/settings/company/{nit}        — retrieve settings for a company
  PUT  /api/v1/settings/company/{nit}        — create or replace settings (manual rates)
  POST /api/v1/settings/company/{nit}/setup  — auto-compute rates from city/CIIU/régimen
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.gemini_client import get_gemini_client
from app.models.schemas import (
    CompanyProfileSetupRequest,
    CompanySettingsRequest,
    CompanySettingsResponse,
)
from app.services import db_service
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.put("/company/{nit}", response_model=CompanySettingsResponse)
def upsert_company_settings(
    nit: str,
    body: CompanySettingsRequest,
    db: Session = Depends(get_db),
):
    """Create or fully replace the tax configuration for the given company NIT."""
    return db_service.upsert_company_settings(db, nit, body.model_dump())


@router.post("/company/{nit}/setup", response_model=CompanySettingsResponse)
def setup_company_tax_profile(
    nit: str,
    body: CompanyProfileSetupRequest,
    db: Session = Depends(get_db),
):
    """
    Auto-compute and save the correct Colombian tax rates for a company.

    The user provides their city, CIIU code, and IVA régimen.
    The agent queries the normative RAG and asks Gemini to determine the
    applicable Retefuente, ReteICA, and IVA rates, then saves them.

    This replaces the need to know the raw decimal rates — the user just
    answers three simple questions about their company.
    """
    # Step 1: Query RAG normativo for city/CIIU-specific rate information
    rag_context = ""
    try:
        rag = get_rag_service()
        queries = [
            f"tasa ReteICA municipio {body.ciudad} impuesto industria comercio",
            f"retención en la fuente actividad CIIU {body.codigo_ciiu}",
            f"tarifas retención IVA régimen {'común' if body.iva_responsable else 'simplificado'}",
        ]
        results = []
        for q in queries:
            results.extend(rag.search_normativo(q, n_results=3))
        rag_context = "\n\n".join(
            f"[{r.metadata.get('source', 'normativa')}]: {r.content}"
            for r in results
        )
        logger.info(
            f"Tax profile setup: RAG returned {len(results)} chunks "
            f"for ciudad={body.ciudad}, ciiu={body.codigo_ciiu}"
        )
    except Exception as rag_err:
        logger.warning(f"Tax profile setup: RAG lookup failed ({rag_err}), proceeding with defaults")

    # Step 2: Ask Gemini to determine the correct rates
    try:
        gemini = get_gemini_client()
        rate_lookup = gemini.compute_tax_rates_from_profile(
            ciudad=body.ciudad,
            codigo_ciiu=body.codigo_ciiu,
            iva_responsable=body.iva_responsable,
            rag_context=rag_context,
        )
    except Exception as gemini_err:
        logger.warning(f"Tax profile setup: Gemini failed ({gemini_err}), using national defaults")
        # Fall back to national defaults
        from app.core.gemini_client import TaxRateLookup
        rate_lookup = TaxRateLookup(
            tasa_retefuente_servicios=0.11,
            tasa_retefuente_bienes=0.03,
            tasa_retefuente_arrendamiento=0.10,
            tasa_reteica=0.0069,
            tasa_iva_general=0.19 if body.iva_responsable else 0.0,
            fuentes=["Art. 383 ET", "Art. 401 ET", "Art. 477 ET"],
        )

    # Step 3: Persist to company_settings
    settings_data = {
        "nombre": body.nombre,
        "ciudad": body.ciudad,
        "codigo_ciiu": body.codigo_ciiu,
        "iva_responsable": body.iva_responsable,
        "tasa_retefuente_servicios": rate_lookup.tasa_retefuente_servicios,
        "tasa_retefuente_bienes": rate_lookup.tasa_retefuente_bienes,
        "tasa_retefuente_arrendamiento": rate_lookup.tasa_retefuente_arrendamiento,
        "tasa_reteica": rate_lookup.tasa_reteica,
        "tasa_iva_general": rate_lookup.tasa_iva_general,
    }

    row = db_service.upsert_company_settings(db, nit, settings_data)
    logger.info(
        f"Tax profile setup complete for NIT {nit}: "
        f"reteica={rate_lookup.tasa_reteica}, "
        f"retefuente_servicios={rate_lookup.tasa_retefuente_servicios}"
    )
    return row
