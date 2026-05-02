"""
PUC (Plan Único de Cuentas) CRUD endpoints.

Endpoints:
  GET  /api/v1/puc              — list all PUC accounts (with search/include_inactive filters)
  GET  /api/v1/puc/{codigo}     — get one PUC account
  POST /api/v1/puc              — create new PUC account
  PUT  /api/v1/puc/{codigo}     — update existing PUC account
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database import CuentaPUC
from app.models.schemas import CuentaPUCRequest, CuentaPUCResponse
from app.services import db_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[CuentaPUCResponse])
def list_puc(
    search: str = "",
    include_inactive: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """List PUC accounts with optional search and filter for inactive."""
    if search:
        return db_service.search_puc(db, search, limit, include_inactive=include_inactive)
    if include_inactive:
        return db_service.get_all_puc_including_inactive(db)[:limit]
    return db_service.get_all_puc(db)[:limit]


@router.get("/{codigo}", response_model=CuentaPUCResponse)
def get_puc(codigo: str, db: Session = Depends(get_db)):
    """Get a single PUC account by code."""
    row = db.query(CuentaPUC).filter(CuentaPUC.codigo == codigo).first()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"PUC code '{codigo}' not found"
        )
    return row


@router.post("", response_model=CuentaPUCResponse, status_code=201)
def create_puc(body: CuentaPUCRequest, db: Session = Depends(get_db)):
    """Create a new PUC account."""
    try:
        return db_service.create_puc(db, body.model_dump())
    except ValueError as e:
        logger.warning(f"PUC create conflict: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"PUC create error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create PUC account")


@router.put("/{codigo}", response_model=CuentaPUCResponse)
def update_puc(
    codigo: str, body: CuentaPUCRequest, db: Session = Depends(get_db)
):
    """Update an existing PUC account."""
    if body.codigo != codigo:
        raise HTTPException(
            status_code=400,
            detail="Path parameter 'codigo' must match body field 'codigo'",
        )

    row = db_service.update_puc(db, codigo, body.model_dump())
    if not row:
        raise HTTPException(
            status_code=404, detail=f"PUC code '{codigo}' not found"
        )
    logger.info(f"PUC updated: {codigo}")
    return row
