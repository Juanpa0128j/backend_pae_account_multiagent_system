from fastapi import APIRouter, Query, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import db_service
from app.models.database import TransactionStatus

router = APIRouter()


class TransactionListItem(BaseModel):
    id: str
    fecha: str
    concepto: str
    total: float
    status: str
    nit_emisor: str


@router.get("/", response_model=List[TransactionListItem])
async def list_transactions(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Returns a list of transactions from the database, optionally filtered by status.
    """
    txn_status = None
    if status:
        try:
            txn_status = TransactionStatus(status.lower())
        except ValueError:
            pass

    txns = db_service.get_transactions_by_status(db, txn_status, limit, offset)

    return [
        TransactionListItem(
            id=t.id,
            fecha=str(t.fecha) if t.fecha else "",
            concepto=t.descripcion or "",
            total=float(t.total) if t.total else 0,
            status=t.status.value if t.status else "unknown",
            nit_emisor=t.nit_emisor or "",
        )
        for t in txns
    ]


@router.get("/search")
async def search_transactions(
    nit: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """Search transactions with multiple filters."""
    from datetime import datetime

    fi = None
    ff = None
    if fecha_inicio:
        try:
            fi = datetime.fromisoformat(fecha_inicio)
        except ValueError:
            pass
    if fecha_fin:
        try:
            ff = datetime.fromisoformat(fecha_fin)
        except ValueError:
            pass

    txn_status = None
    if status:
        try:
            txn_status = TransactionStatus(status.lower())
        except ValueError:
            pass

    txns = db_service.search_transactions(db, nit, fi, ff, txn_status, limit)
    return [
        {
            "id": t.id,
            "fecha": str(t.fecha) if t.fecha else "",
            "concepto": t.descripcion or "",
            "total": float(t.total) if t.total else 0,
            "status": t.status.value if t.status else "unknown",
            "nit_emisor": t.nit_emisor or "",
        }
        for t in txns
    ]


@router.get("/{id}")
async def get_transaction(id: str, db: Session = Depends(get_db)):
    """Returns a single transaction by ID."""
    from app.models.database import TransactionPending

    txn = db.query(TransactionPending).filter(TransactionPending.id == id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction {id} not found")

    return {
        "id": txn.id,
        "fecha": str(txn.fecha) if txn.fecha else "",
        "concepto": txn.descripcion or "",
        "total": float(txn.total) if txn.total else 0,
        "status": txn.status.value if txn.status else "unknown",
        "nit_emisor": txn.nit_emisor or "",
        "items": txn.items,
        "raw_data": txn.raw_data,
    }
