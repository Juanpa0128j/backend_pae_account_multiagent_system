from fastapi import APIRouter, Query, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.services import db_service
from app.services.nit_utils import normalize_optional_nit
from app.services.parse_utils import safe_float
from app.models.database import FinancialStatement, TransactionStatus

router = APIRouter()


class TransactionListItem(BaseModel):
    id: str
    fecha: str
    concepto: str
    total: float
    status: str
    nit_emisor: str
    ingest_id: Optional[str] = None
    source: Optional[str] = None  # 'via_a' (default) or 'via_b_libro_auxiliar'


def _libro_auxiliar_lines_as_transactions(
    db: Session, company_nit: str, limit: int, offset: int
) -> List[TransactionListItem]:
    """For Vía B-locked companies, surface libro_auxiliar lines as transactions.

    The user has no posted transactions of their own — they uploaded an
    aggregated ledger. Each line in that ledger is a movement we can render
    in the same shape as Vía A transactions for UI consistency.
    """
    stmt = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.entity_nit == company_nit,
            FinancialStatement.statement_type == "libro_auxiliar",
        )
        .order_by(FinancialStatement.period_end.desc())
        .first()
    )
    if stmt is None or not isinstance(stmt.data, dict):
        return []

    lines = stmt.data.get("lines") or stmt.data.get("accounts") or []
    if not isinstance(lines, list):
        return []

    out: List[TransactionListItem] = []
    for idx, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        debito = safe_float(line.get("debito"))
        credito = safe_float(line.get("credito"))
        total = debito if debito > 0 else credito
        concepto_parts = []
        if line.get("cuenta_puc") or line.get("cuenta_nombre"):
            concepto_parts.append(
                f"{line.get('cuenta_puc') or ''} {line.get('cuenta_nombre') or ''}".strip()
            )
        if line.get("detalle"):
            concepto_parts.append(str(line["detalle"]))
        elif line.get("comprobante"):
            concepto_parts.append(f"Comp: {line['comprobante']}")
        out.append(
            TransactionListItem(
                id=f"vbla_{stmt.id}_{idx}",
                fecha=str(line.get("fecha") or ""),
                concepto=" — ".join(concepto_parts) or "Movimiento libro auxiliar",
                total=total,
                status="posted",
                nit_emisor=str(line.get("tercero_nit") or ""),
                ingest_id=stmt.ingest_id,
                source="via_b_libro_auxiliar",
            )
        )
    return out[offset : offset + limit]


@router.get("/", response_model=List[TransactionListItem])
async def list_transactions(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    company_nit: Optional[str] = Query(None, description="Filter by company NIT"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Returns a list of transactions from the database, optionally filtered by status.

    For Vía B-locked companies (no posted transactions exist), returns
    libro_auxiliar lines mapped to the same shape so the UI stays consistent.
    """
    # Normalize the company NIT consistently with /reports/* and /dashboard/* —
    # otherwise a NIT with dots/spaces would silently miss the lock check.
    normalized_company_nit = (
        normalize_optional_nit(company_nit) if company_nit else None
    )

    # Vía B branch: when the company is locked to 'work_with_existing', surface
    # libro_auxiliar lines instead of posted transactions.
    if normalized_company_nit:
        try:
            locked = db_service.get_company_locked_pathway(db, normalized_company_nit)
        except Exception:
            locked = None
        if locked == "work_with_existing":
            return _libro_auxiliar_lines_as_transactions(
                db, normalized_company_nit, limit, offset
            )

    txn_status = None
    if status:
        try:
            txn_status = TransactionStatus(status.lower())
        except ValueError:
            pass

    txns = db_service.get_transactions_by_status(
        db, txn_status, limit, offset, normalized_company_nit
    )

    return [
        TransactionListItem(
            id=t.id,
            fecha=str(t.fecha) if t.fecha else "",
            concepto=t.descripcion or "",
            total=float(t.total) if t.total else 0,
            status=t.status.value if t.status else "unknown",
            nit_emisor=t.nit_emisor or "",
            ingest_id=t.ingest_id,
            source="via_a",
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
    current_user: CurrentUser = Depends(get_current_user),
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
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status value: {status}",
            )

    txns = db_service.search_transactions(db, nit, fi, ff, txn_status, limit)
    return [
        {
            "id": t.id,
            "fecha": str(t.fecha) if t.fecha else "",
            "concepto": t.descripcion or "",
            "total": float(t.total) if t.total else 0,
            "status": t.status.value if t.status else "unknown",
            "nit_emisor": t.nit_emisor or "",
            "ingest_id": t.ingest_id,
        }
        for t in txns
    ]


@router.get("/{id}")
async def get_transaction(
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return a single transaction with its posted classification + journal entry lines.

    The detail UI needs the full picture of one ingest:
      - The pending transaction itself (raw extracted totals, file origin).
      - The posted classification (cuenta_puc principal, taxes, agent reasoning).
      - All journal_entry_lines for the asiento (so multi-line CE / RC / payroll
        comprobantes show every debit and credit, not just the principal PUC).
    """
    from app.models.database import (
        JournalEntryLine,
        TransactionPending,
        TransactionPosted,
    )

    txn = db.query(TransactionPending).filter(TransactionPending.id == id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction {id} not found")

    posted = (
        db.query(TransactionPosted)
        .filter(TransactionPosted.transaction_pending_id == id)
        .order_by(TransactionPosted.created_at.desc())
        .first()
    )

    from app.models.database import ProcessJob

    process_id: str | None = None
    if txn.ingest_id:
        pj = (
            db.query(ProcessJob)
            .filter(ProcessJob.ingest_id == txn.ingest_id)
            .order_by(ProcessJob.created_at.desc())
            .first()
        )
        process_id = pj.id if pj else None

    journal_lines: list[dict] = []
    if posted is not None:
        lines = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.transaction_posted_id == posted.id)
            .order_by(JournalEntryLine.id)
            .all()
        )
        journal_lines = [
            {
                "id": line.id,
                "cuenta_puc": line.cuenta_puc,
                "descripcion": line.descripcion or "",
                "tercero_nit": getattr(line, "tercero_nit", "") or "",
                "debito": float(line.debito or 0),
                "credito": float(line.credito or 0),
                "fecha": str(getattr(line, "fecha", "") or ""),
            }
            for line in lines
        ]

    posted_payload: dict | None = None
    if posted is not None:
        posted_payload = {
            "id": posted.id,
            "cuenta_puc": posted.cuenta_puc,
            "puc_descripcion": posted.puc_descripcion or "",
            "retefuente": float(posted.retefuente or 0),
            "reteica": float(posted.reteica or 0),
            "iva": float(posted.iva or 0),
            "ica": float(posted.ica or 0),
            "provision_renta": float(posted.provision_renta or 0),
            "neto_a_pagar": float(posted.neto_a_pagar or 0),
            "journal_entries_json": posted.journal_entries_json,
            "tax_references": posted.tax_references,
            "agent_reasoning": posted.agent_reasoning,
            "status": posted.status.value if posted.status else "unknown",
        }

    return {
        "id": txn.id,
        "fecha": str(txn.fecha) if txn.fecha else "",
        "concepto": txn.descripcion or "",
        "total": float(txn.total) if txn.total else 0,
        "status": txn.status.value if txn.status else "unknown",
        "nit_emisor": txn.nit_emisor or "",
        "items": txn.items,
        "raw_data": txn.raw_data,
        "posted": posted_payload,
        "journal_lines": journal_lines,
        "process_id": process_id,
    }
