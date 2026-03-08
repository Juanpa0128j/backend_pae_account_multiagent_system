"""
Company settings API — manage per-tenant tax configuration.

Endpoints:
  GET  /api/v1/settings/company/{nit}  — retrieve settings for a company
  PUT  /api/v1/settings/company/{nit}  — create or replace settings for a company
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.schemas import CompanySettingsRequest, CompanySettingsResponse
from app.services import db_service

router = APIRouter()


@router.get("/company/{nit}", response_model=CompanySettingsResponse)
def get_company_settings(nit: str, db: Session = Depends(get_db)):
    """Return the tax configuration for the given company NIT."""
    row = db_service.get_company_settings(db, nit)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No tax settings found for NIT '{nit}'. "
                   "Use PUT to create them.",
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
