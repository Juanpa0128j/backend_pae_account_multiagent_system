from fastapi import APIRouter, HTTPException, Query, Depends
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import db_service
from app.services.nit_utils import normalize_nit

router = APIRouter()


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string to datetime."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        return None


@router.get("/")
async def get_books(
    tipo: str = Query(..., description="diario, mayor, auxiliar, or balance"),
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    cuenta_puc: Optional[str] = None,
    tercero_nit: Optional[str] = None,
    company_nit: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Any:
    """
    Queries the accounting books (Diario, Mayor, Auxiliar, Balance General).
    Data is read from PostgreSQL journal_entry_lines table.
    """
    fi = _parse_date(fecha_inicio)
    ff = _parse_date(fecha_fin)
    normalized_company_nit = None
    if company_nit:
        try:
            normalized_company_nit = normalize_nit(company_nit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid company_nit: {e}")

    if tipo == "diario":
        lines = db_service.get_daily_journal(db, fi, ff, normalized_company_nit)
        return [
            {
                "fecha": str(l.fecha) if l.fecha else "",
                "comprobante": l.comprobante or "",
                "cuenta": l.cuenta_puc,
                "descripcion": l.descripcion or l.cuenta_nombre or "",
                "debito": float(l.debito),
                "credito": float(l.credito),
            }
            for l in lines
        ]

    elif tipo == "mayor":
        return db_service.get_general_ledger(db, fi, ff, normalized_company_nit)

    elif tipo == "auxiliar":
        if not cuenta_puc:
            return {"error": "cuenta_puc is required for auxiliar"}
        lines = db_service.get_subsidiary_journal(
            db, cuenta_puc, fi, ff, normalized_company_nit
        )
        return [
            {
                "fecha": str(l.fecha) if l.fecha else "",
                "comprobante": l.comprobante or "",
                "tercero_nit": l.tercero_nit or "",
                "descripcion": l.descripcion or "",
                "debito": float(l.debito),
                "credito": float(l.credito),
            }
            for l in lines
        ]

    elif tipo == "balance":
        return db_service.get_balance_sheet(db, ff, normalized_company_nit)

    else:
        return {"error": f"Unknown book type: {tipo}. Use diario, mayor, auxiliar, or balance."}
