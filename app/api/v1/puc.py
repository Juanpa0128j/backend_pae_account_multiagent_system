"""
PUC (Plan Único de Cuentas) CRUD endpoints.

Endpoints:
  GET    /api/v1/puc              — list all PUC accounts (with search/include_inactive filters)
  GET    /api/v1/puc/{codigo}     — get one PUC account
  POST   /api/v1/puc              — create new PUC account
  PUT    /api/v1/puc/{codigo}     — update existing PUC account
  DELETE /api/v1/puc/{codigo}     — soft-delete (sets activa=False)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.limiter import limiter
from app.core.database import get_db
from app.models.database import CuentaPUC
from app.models.schemas import CuentaPUCRequest, CuentaPUCResponse
from app.services import db_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[CuentaPUCResponse])
@limiter.limit("60/minute")
def list_puc(
    request: Request,
    search: str = "",
    include_inactive: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List PUC accounts with optional search and filter for inactive."""
    if search:
        return db_service.search_puc(
            db, search, limit, include_inactive=include_inactive
        )
    if include_inactive:
        return db_service.get_all_puc_including_inactive(db)[:limit]
    return db_service.get_all_puc(db)[:limit]


@router.get("/{codigo}", response_model=CuentaPUCResponse)
@limiter.limit("60/minute")
def get_puc(
    request: Request,
    codigo: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a single PUC account by code."""
    row = db.query(CuentaPUC).filter(CuentaPUC.codigo == codigo).first()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"El código PUC '{codigo}' no fue encontrado."
        )
    return row


@router.post("", response_model=CuentaPUCResponse, status_code=201)
@limiter.limit("30/minute")
def create_puc(
    request: Request,
    body: CuentaPUCRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new PUC account."""
    try:
        return db_service.create_puc(db, body.model_dump())
    except ValueError as e:
        logger.warning(f"PUC create conflict: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"PUC create error: {e}")
        raise HTTPException(status_code=500, detail="Error al crear la cuenta PUC")


@router.put("/{codigo}", response_model=CuentaPUCResponse)
@limiter.limit("30/minute")
def update_puc(
    request: Request,
    codigo: str,
    body: CuentaPUCRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update an existing PUC account."""
    if body.codigo != codigo:
        raise HTTPException(
            status_code=400,
            detail="El parámetro 'codigo' en la URL debe coincidir con el campo 'codigo' del cuerpo",
        )

    row = db_service.update_puc(db, codigo, body.model_dump())
    if not row:
        raise HTTPException(
            status_code=404, detail=f"El código PUC '{codigo}' no fue encontrado."
        )
    logger.info(f"PUC updated: {codigo}")
    return row


@router.delete("/{codigo}", status_code=204)
@limiter.limit("30/minute")
def delete_puc(
    request: Request,
    codigo: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Soft-delete a PUC account (sets deleted_at). Returns 404 if not found."""
    found = db_service.soft_delete_cuenta_puc(db, codigo)
    if not found:
        raise HTTPException(
            status_code=404, detail=f"El código PUC '{codigo}' no fue encontrado."
        )
    logger.info(f"PUC soft-deleted: {codigo}")
    return Response(status_code=204)


@router.post("/{id}/restore", response_model=CuentaPUCResponse, status_code=200)
@limiter.limit("30/minute")
def restore_puc(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Restore a soft-deleted PUC account."""
    row = db_service.restore_cuenta_puc(db, id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Cuenta PUC no encontrada o ya activa.",
        )
    logger.info(f"PUC restored: {id}")
    return row
