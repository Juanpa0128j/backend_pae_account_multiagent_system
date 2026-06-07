from fastapi import APIRouter, Query, Depends, HTTPException, Body
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.logger import get_logger
from app.services import db_service
from app.services.document_mappers import safe_datetime
from app.services.nit_utils import normalize_nit, normalize_optional_nit
from app.services.parse_utils import safe_float
from app.models.database import (
    FinancialStatement,
    TransactionPending,
    TransactionStatus,
)

logger = get_logger("app.api.transactions")

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


def _delete_transaction_cascade(db: Session, txn_id: str) -> Optional[str]:
    """Delete a TransactionPending and all its child records (posted + journal lines).

    Returns the normalized owning company NIT (if resolvable) so callers can
    re-sync the derived financial statements after the cascade. The NIT is read
    BEFORE the rows are removed: prefer the pending's own ``company_nit``, then
    fall back to the posted row's ``company_nit``.
    """
    from app.models.database import (
        JournalEntryLine,
        TransactionPending,
        TransactionPosted,
    )

    txn = db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")

    # Re-processed transactions may have multiple posted rows for a single
    # pending (see get_transaction's order_by created_at.desc()). Delete every
    # posted row and its journal lines to avoid orphans.
    posted_rows = (
        db.query(TransactionPosted)
        .filter(TransactionPosted.transaction_pending_id == txn_id)
        .all()
    )

    # Capture the owning company NIT before deleting (pending preferred, posted
    # as fallback). Normalize so the value matches what reports/derivation use.
    raw_nit = getattr(txn, "company_nit", None) or next(
        (getattr(p, "company_nit", None) for p in posted_rows if p.company_nit), None
    )
    company_nit = normalize_nit(raw_nit) if raw_nit else None

    for posted in posted_rows:
        db.query(JournalEntryLine).filter(
            JournalEntryLine.transaction_posted_id == posted.id
        ).delete(synchronize_session=False)
        db.delete(posted)

    db.delete(txn)
    return company_nit


def _resync_derived_statements(db: Session, company_nit: Optional[str]) -> None:
    """Refresh-in-place (or clear) journal-derived statements after a delete.

    Vía A derivation is manual (the user picks a period and generates first-level
    statements, then derives NIC 7 secondaries). A transaction delete invalidates
    whatever the user already generated, so we refresh ONLY the periods that
    already have ``derived_from_journal`` rows — keeping their original period
    bounds and ``frequency`` — instead of inventing new periods.

    - If journal entries remain: for each distinct (period_start, period_end)
      among existing first-level rows, delete that period's derived rows
      (first-level ``derived_from_journal`` + secondary ``derived``), rebuild
      first-level with the preserved frequency, and re-derive the NIC 7
      secondaries if that period had them before.
    - If NO journal entries remain: purge every ``derived_from_journal`` row for
      the NIT so reports go empty instead of showing a stale snapshot. Vía B
      uploads (source_mode ``direct`` / ``derived``) are left untouched.

    Non-fatal: logs a warning and never raises. Transaction deletion must
    succeed even if the resync fails.
    """
    if not company_nit:
        return

    from app.models.database import JournalEntryLine

    try:
        remaining = (
            db.query(JournalEntryLine.id)
            .filter(JournalEntryLine.company_nit == company_nit)
            .first()
        )
        if remaining is None:
            # No journal left — purge ALL journal-derived statements so reports go
            # empty: both first-level (derived_from_journal) AND the NIC 7
            # secondaries derived from them (derived). Otherwise stale flujo /
            # cambios / notas would keep showing for periods whose journal is gone.
            # Vía B uploads (source_mode='direct') are left untouched.
            db.query(FinancialStatement).filter(
                FinancialStatement.entity_nit == company_nit,
                FinancialStatement.source_mode.in_(("derived_from_journal", "derived")),
            ).delete(synchronize_session=False)
            db.commit()
            return

        # Journal remains — refresh in place only the periods the user already
        # generated, preserving each period's frequency.
        first_level_rows = (
            db.query(
                FinancialStatement.period_start,
                FinancialStatement.period_end,
                FinancialStatement.frequency,
            )
            .filter(
                FinancialStatement.entity_nit == company_nit,
                FinancialStatement.source_mode == "derived_from_journal",
            )
            .all()
        )
        # Distinct (period_start, period_end) → frequency (first non-null wins).
        periods: dict[tuple, Optional[str]] = {}
        for ps, pe, freq in first_level_rows:
            if ps is None or pe is None:
                continue
            key = (ps, pe)
            if key not in periods or (periods[key] is None and freq is not None):
                periods[key] = freq

        if not periods:
            return

        from app.services.financial_statement_service import (
            BusinessRuleError,
            build_first_level_from_journal_entries,
            derive_financial_statements,
        )

        for (ps, pe), freq in periods.items():
            # Did this period already have NIC 7 secondaries?
            had_secondary = (
                db.query(FinancialStatement.id)
                .filter(
                    FinancialStatement.entity_nit == company_nit,
                    FinancialStatement.source_mode == "derived",
                    FinancialStatement.period_start == ps,
                    FinancialStatement.period_end == pe,
                )
                .first()
                is not None
            )
            # Delete this period's derived rows (build_first_level skips existing
            # types, so we must clear before rebuilding to truly refresh).
            db.query(FinancialStatement).filter(
                FinancialStatement.entity_nit == company_nit,
                FinancialStatement.source_mode.in_(("derived_from_journal", "derived")),
                FinancialStatement.period_start == ps,
                FinancialStatement.period_end == pe,
            ).delete(synchronize_session=False)
            db.commit()

            build_first_level_from_journal_entries(
                db,
                company_nit=company_nit,
                period_start=ps,
                period_end=pe,
                frequency=freq,
            )
            db.commit()

            if had_secondary:
                try:
                    derive_financial_statements(
                        company_nit=company_nit,
                        period_start=ps,
                        period_end=pe,
                        input_source_mode="derived_from_journal",
                        prior_from_journal=True,
                    )
                except BusinessRuleError as exc:
                    # e.g. the period is no longer annual-eligible after the delete.
                    logger.warning(
                        "transactions: secondary re-derivation skipped for %s "
                        "%s..%s (non-fatal): %s",
                        company_nit,
                        ps,
                        pe,
                        exc,
                    )
    except Exception as exc:  # pragma: no cover - non-fatal guard
        logger.warning(
            "transactions: derived-statement resync failed for %s (non-fatal): %s",
            company_nit,
            exc,
        )


class SetFechaPayload(BaseModel):
    fecha: str  # ISO date (YYYY-MM-DD) or DD/MM/YYYY


@router.patch("/{id}/fecha")
async def set_transaction_fecha(
    id: str,
    payload: SetFechaPayload = Body(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Set or update a TransactionPending.fecha.

    Used by the HITL pending-review flow when the contador couldn't extract a
    date and the auditor blocked the persist with rule ``ING-FECHA-MISSING``.
    After the user supplies a date here, the frontend retriggers persistence
    via ``POST /api/v1/process/{process_id}/audit-confirm``.
    """
    txn = db.query(TransactionPending).filter(TransactionPending.id == id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction {id} not found")

    # Once the transaction has been posted, its fecha is already replicated to
    # JournalEntryLine.fecha and into the derived FinancialStatement periods.
    # Letting the user patch it here without cascading would leave the pending
    # row out of sync with the posted ledger — reject and require a full
    # re-process flow instead.
    if txn.status == TransactionStatus.POSTED:
        raise HTTPException(
            status_code=409,
            detail=(
                "La transacción ya fue contabilizada; su fecha no puede modificarse "
                "directamente sin re-procesar el asiento. Anule el asiento y vuelva a "
                "procesarlo con la fecha correcta."
            ),
        )

    parsed = safe_datetime(payload.fecha)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not parse fecha '{payload.fecha}'. Use YYYY-MM-DD, "
                "YYYY-MM, or DD/MM/YYYY."
            ),
        )

    txn.fecha = parsed
    raw = dict(txn.raw_data or {})
    raw["fecha"] = parsed.date().isoformat()
    raw.pop("needs_user_fecha", None)
    txn.raw_data = raw

    db.commit()
    db.refresh(txn)
    return {
        "id": txn.id,
        "fecha": txn.fecha.isoformat() if txn.fecha else None,
    }


@router.delete("/{id}", status_code=204)
async def delete_transaction(
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a single transaction and all its associated records."""
    company_nit = _delete_transaction_cascade(db, id)
    _resync_derived_statements(db, company_nit)
    db.commit()


@router.delete("/by-ingest/{ingest_id}", status_code=200)
async def delete_transactions_by_ingest(
    ingest_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all transactions belonging to a specific ingest document."""
    from app.models.database import TransactionPending

    txn_ids = [
        row.id
        for row in db.query(TransactionPending.id)
        .filter(TransactionPending.ingest_id == ingest_id)
        .all()
    ]
    if not txn_ids:
        raise HTTPException(
            status_code=404, detail=f"No transactions found for ingest {ingest_id}"
        )

    affected_nits: set[str] = set()
    for txn_id in txn_ids:
        company_nit = _delete_transaction_cascade(db, txn_id)
        if company_nit:
            affected_nits.add(company_nit)

    # Resync each affected company once (not per-transaction) so the derived
    # statements reflect the post-delete journal state.
    for company_nit in affected_nits:
        _resync_derived_statements(db, company_nit)

    db.commit()
    return {"deleted": len(txn_ids)}
