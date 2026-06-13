"""
Special Taxes API — per-company configurable withholding taxes (estampilla, timbre, etc.).

Endpoints:
  GET    /api/v1/settings/special-taxes?company_nit=         — list all (active+inactive)
  POST   /api/v1/settings/special-taxes                      — create
  GET    /api/v1/settings/special-taxes/{id}                 — get one
  PUT    /api/v1/settings/special-taxes/{id}                 — update
  DELETE /api/v1/settings/special-taxes/{id}                 — soft-delete
  GET    /api/v1/settings/special-taxes/{id}/accumulators    — list periods
  POST   /api/v1/settings/special-taxes/{id}/liquidar        — liquidate a period
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.limiter import limiter
from app.services import db_service
from app.services.nit_utils import normalize_nit

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────


class SpecialTaxCreate(BaseModel):
    company_nit: str
    code: str = Field(..., max_length=64)
    nombre: str = Field(..., max_length=255)
    descripcion: Optional[str] = None
    rate: Decimal = Field(..., gt=0, le=1)
    base_calc: str = Field(..., pattern="^(total_pago|base_gravable|custom)$")
    base_calc_formula: Optional[str] = None
    applies_to_doc_types: list[str] = Field(default_factory=list)
    es_entidad_publica_only: bool = False
    settlement: str = Field(
        default="per_transaction", pattern="^(per_transaction|periodic)$"
    )
    cuenta_gasto: str = Field(..., max_length=10)
    cuenta_por_pagar: str = Field(..., max_length=10)
    norma_referencia: Optional[str] = None
    vigente_desde: Optional[date] = None
    vigente_hasta: Optional[date] = None
    activo: bool = True


class SpecialTaxUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    rate: Optional[Decimal] = None
    base_calc: Optional[str] = None
    base_calc_formula: Optional[str] = None
    applies_to_doc_types: Optional[list[str]] = None
    es_entidad_publica_only: Optional[bool] = None
    settlement: Optional[str] = None
    cuenta_gasto: Optional[str] = None
    cuenta_por_pagar: Optional[str] = None
    norma_referencia: Optional[str] = None
    vigente_desde: Optional[date] = None
    vigente_hasta: Optional[date] = None
    activo: Optional[bool] = None


class SpecialTaxResponse(BaseModel):
    id: str
    company_nit: str
    code: str
    nombre: str
    descripcion: Optional[str]
    rate: Decimal
    base_calc: str
    base_calc_formula: Optional[str]
    applies_to_doc_types: list[str]
    es_entidad_publica_only: bool
    settlement: str
    cuenta_gasto: str
    cuenta_por_pagar: str
    norma_referencia: Optional[str]
    vigente_desde: Optional[date]
    vigente_hasta: Optional[date]
    activo: bool

    model_config = {"from_attributes": True}


class AccumulatorResponse(BaseModel):
    id: str
    special_tax_id: str
    company_nit: str
    period_year: int
    period_month: int
    accumulated_base: str
    accumulated_tax: str
    liquidated: bool
    liquidated_at: Optional[str]

    model_config = {"from_attributes": True}


class LiquidarRequest(BaseModel):
    company_nit: str
    year: int = Field(..., ge=2020, le=2100)
    month: int = Field(..., ge=1, le=12)


class LiquidarResponse(BaseModel):
    id: str
    special_tax_id: str
    company_nit: str
    period_year: int
    period_month: int
    accumulated_base: str
    accumulated_tax: str
    liquidated: bool
    liquidated_at: Optional[str]


def _tax_to_response(row) -> SpecialTaxResponse:
    return SpecialTaxResponse(
        id=str(row.id),
        company_nit=row.company_nit,
        code=row.code,
        nombre=row.nombre,
        descripcion=row.descripcion,
        rate=row.rate,
        base_calc=row.base_calc,
        base_calc_formula=row.base_calc_formula,
        applies_to_doc_types=row.applies_to_doc_types or [],
        es_entidad_publica_only=bool(row.es_entidad_publica_only),
        settlement=row.settlement,
        cuenta_gasto=row.cuenta_gasto,
        cuenta_por_pagar=row.cuenta_por_pagar,
        norma_referencia=row.norma_referencia,
        vigente_desde=row.vigente_desde,
        vigente_hasta=row.vigente_hasta,
        activo=bool(row.activo),
    )


def _acc_to_dict(acc) -> dict:
    return {
        "id": str(acc.id),
        "special_tax_id": str(acc.special_tax_id),
        "company_nit": acc.company_nit,
        "period_year": acc.period_year,
        "period_month": acc.period_month,
        "accumulated_base": f"{acc.accumulated_base:.2f}",
        "accumulated_tax": f"{acc.accumulated_tax:.2f}",
        "liquidated": bool(acc.liquidated),
        "liquidated_at": acc.liquidated_at.isoformat() if acc.liquidated_at else None,
    }


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/special-taxes", response_model=list[SpecialTaxResponse])
@limiter.limit("60/minute")
def list_special_taxes(
    request: Request,
    company_nit: str = Query(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all special taxes for a company (active and inactive)."""
    nit = normalize_nit(company_nit)
    rows = db_service.list_special_taxes(db, nit)
    return [_tax_to_response(r) for r in rows]


@router.post("/special-taxes", response_model=SpecialTaxResponse, status_code=201)
@limiter.limit("60/minute")
def create_special_tax(
    request: Request,
    body: SpecialTaxCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new special tax configuration."""
    nit = normalize_nit(body.company_nit)
    try:
        row = db_service.create_special_tax(
            db=db,
            company_nit=nit,
            code=body.code,
            nombre=body.nombre,
            rate=body.rate,
            base_calc=body.base_calc,
            cuenta_gasto=body.cuenta_gasto,
            cuenta_por_pagar=body.cuenta_por_pagar,
            descripcion=body.descripcion,
            base_calc_formula=body.base_calc_formula,
            applies_to_doc_types=body.applies_to_doc_types,
            es_entidad_publica_only=body.es_entidad_publica_only,
            settlement=body.settlement,
            norma_referencia=body.norma_referencia,
            vigente_desde=body.vigente_desde,
            vigente_hasta=body.vigente_hasta,
            activo=body.activo,
        )
    except Exception as exc:
        logger.error("create_special_tax failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _tax_to_response(row)


@router.get("/special-taxes/{tax_id}", response_model=SpecialTaxResponse)
@limiter.limit("60/minute")
def get_special_tax(
    request: Request,
    tax_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a single special tax by id."""
    row = db_service.get_special_tax(db, tax_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Special tax not found")
    return _tax_to_response(row)


@router.put("/special-taxes/{tax_id}", response_model=SpecialTaxResponse)
@limiter.limit("60/minute")
def update_special_tax(
    request: Request,
    tax_id: str,
    body: SpecialTaxUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update fields on an existing special tax."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    row = db_service.update_special_tax(db, tax_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="Special tax not found")
    return _tax_to_response(row)


@router.delete("/special-taxes/{tax_id}", status_code=204)
@limiter.limit("60/minute")
def delete_special_tax(
    request: Request,
    tax_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Soft-delete a special tax (sets activo=False)."""
    found = db_service.delete_special_tax(db, tax_id)
    if not found:
        raise HTTPException(status_code=404, detail="Special tax not found")


@router.get(
    "/special-taxes/{tax_id}/accumulators",
    response_model=list[AccumulatorResponse],
)
@limiter.limit("60/minute")
def list_accumulators(
    request: Request,
    tax_id: str,
    company_nit: str = Query(...),
    only_unliquidated: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List accumulator periods for a special tax."""
    nit = normalize_nit(company_nit)
    rows = db_service.list_accumulators(db, tax_id, nit, only_unliquidated)
    return [AccumulatorResponse(**_acc_to_dict(r)) for r in rows]


@router.post("/special-taxes/{tax_id}/liquidar")
@limiter.limit("30/minute")
def liquidar_special_tax(
    request: Request,
    tax_id: str,
    body: LiquidarRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Liquidate a periodic special tax for a given month.

    Marks the accumulator as liquidated. The caller is responsible for
    creating any manual journal entry or using the returned amounts to post it.
    Returns the liquidated accumulator row.
    """
    nit = normalize_nit(body.company_nit)
    acc = db_service.liquidate_accumulator(db, tax_id, nit, body.year, body.month)
    if acc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Accumulator not found for {body.year}-{body.month:02d}",
        )
    return _acc_to_dict(acc)
