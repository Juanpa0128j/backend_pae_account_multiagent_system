from fastapi import APIRouter, Query, Depends, HTTPException, Body, Request
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.logger import get_logger
from app.core.limiter import limiter
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


class TransactionItem(BaseModel):
    descripcion: str
    subtotal: float
    iva: float = 0.0


class CreateTransactionPayload(BaseModel):
    fecha: str
    concepto: str
    total: float
    nit_emisor: str
    nit_receptor: str
    tipo_documento: str
    items: List[TransactionItem] = []
    company_nit: str


class UpdateTransactionPayload(BaseModel):
    fecha: Optional[str] = None
    concepto: Optional[str] = None
    total: Optional[float] = None
    nit_emisor: Optional[str] = None
    nit_receptor: Optional[str] = None
    tipo_documento: Optional[str] = None
    items: Optional[List[TransactionItem]] = None


def _build_raw_data(payload: CreateTransactionPayload) -> dict:
    """Shape user input like a Gemini extraction for pipeline compatibility."""
    items = []
    for it in payload.items:
        items.append(
            {
                "descripcion": it.descripcion,
                "subtotal": it.subtotal,
                "iva": it.iva,
            }
        )
    subtotal = sum(it.subtotal for it in payload.items)
    iva = sum(it.iva for it in payload.items)
    return {
        "fecha": payload.fecha,
        "nit_emisor": payload.nit_emisor,
        "nit_receptor": payload.nit_receptor,
        "totales": {
            "subtotal": subtotal,
            "iva": iva,
            "total": payload.total,
        },
        "items": items,
        "tipo_documento": payload.tipo_documento,
        "concepto": payload.concepto,
    }


@router.post("", status_code=201)
@router.post(
    "/", status_code=201, include_in_schema=False
)  # legacy trailing-slash, no 307
@limiter.limit("30/minute")
async def create_transaction(
    request: Request,
    payload: CreateTransactionPayload,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a manual transaction as a synthetic ingest + pending record."""
    company_nit = normalize_nit(payload.company_nit)

    # Business preconditions
    settings = db_service.get_company_settings(db, company_nit)
    if not settings:
        raise HTTPException(
            status_code=409,
            detail={
                "error_category": "business_precondition",
                "error_code": "MISSING_COMPANY_SETTINGS",
                "message": f"No se encontró configuración tributaria para la empresa con NIT {company_nit}.",
                "remediation": "Configure el perfil tributario de su empresa en /settings y vuelva a intentarlo.",
            },
        )

    # Validate total consistency
    expected_total = sum(it.subtotal + it.iva for it in payload.items)
    if abs(payload.total - expected_total) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                f"El total ({payload.total:.2f}) no coincide con la suma de items + IVA "
                f"({expected_total:.2f})."
            ),
        )

    # Create synthetic ingest job
    ingest_job = db_service.create_manual_ingest_job(
        db, company_nit=company_nit, created_by=str(current_user.id)
    )

    # Build raw_data shape compatible with pipeline
    raw_data = _build_raw_data(payload)

    # Parse fecha
    parsed_fecha = safe_datetime(payload.fecha)
    if parsed_fecha is None:
        raise HTTPException(
            status_code=422,
            detail=f"La fecha '{payload.fecha}' no pudo ser interpretada. Use el formato YYYY-MM-DD o DD/MM/YYYY.",
        )

    # Create pending transaction
    txn = db_service.create_transaction_pending(
        db,
        ingest_id=ingest_job.id,
        fecha=parsed_fecha,
        company_nit=company_nit,
        nit_emisor=normalize_nit(payload.nit_emisor),
        nit_receptor=normalize_nit(payload.nit_receptor),
        total=Decimal(str(payload.total)),
        descripcion=payload.concepto,
        items=raw_data["items"],
        raw_data=raw_data,
        source_file=None,
        commit=True,
    )

    return {
        "transaction_id": txn.id,
        "ingest_id": ingest_job.id,
        "status": txn.status.value,
    }


@router.get("", response_model=List[TransactionListItem])
@router.get(
    "/", response_model=List[TransactionListItem], include_in_schema=False
)  # legacy trailing-slash, no 307
@limiter.limit("60/minute")
async def list_transactions(
    request: Request,
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
@limiter.limit("60/minute")
async def search_transactions(
    request: Request,
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
                detail=f"El valor de estado no es válido: {status}",
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
@limiter.limit("60/minute")
async def get_transaction(
    request: Request,
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
        raise HTTPException(status_code=404, detail=f"Transacción {id} no encontrada.")

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
        raise HTTPException(
            status_code=404, detail=f"Transacción {txn_id} no encontrada."
        )

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
@limiter.limit("30/minute")
async def set_transaction_fecha(
    request: Request,
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
        raise HTTPException(status_code=404, detail=f"Transacción {id} no encontrada.")

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
                f"La fecha '{payload.fecha}' no pudo ser interpretada. Use el formato YYYY-MM-DD, "
                "YYYY-MM o DD/MM/YYYY."
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


@router.patch("/{id}")
@limiter.limit("30/minute")
async def update_transaction(
    request: Request,
    id: str,
    payload: UpdateTransactionPayload = Body(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update a pending transaction. Posted transactions must be reprocessed."""
    from app.models.database import TransactionPending, TransactionStatus

    txn = db.query(TransactionPending).filter(TransactionPending.id == id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transacción {id} no encontrada.")

    if txn.status == TransactionStatus.POSTED:
        raise HTTPException(
            status_code=409,
            detail=(
                "La transacción ya fue contabilizada; su fecha no puede modificarse "
                "directamente sin re-procesar el asiento. Use el endpoint de reprocessamiento."
            ),
        )

    # Validate total consistency if provided
    if payload.total is not None and payload.items is not None:
        expected_total = sum(it.subtotal + it.iva for it in payload.items)
        if abs(payload.total - expected_total) > 1:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"El total ({payload.total:.2f}) no coincide con la suma de items + IVA "
                    f"({expected_total:.2f})."
                ),
            )

    # Rebuild raw_data if any financial fields changed
    raw_data = dict(txn.raw_data or {})
    if payload.concepto is not None:
        raw_data["concepto"] = payload.concepto
    if payload.tipo_documento is not None:
        raw_data["tipo_documento"] = payload.tipo_documento
    if payload.nit_emisor is not None:
        raw_data["nit_emisor"] = payload.nit_emisor
        txn.nit_emisor = normalize_nit(payload.nit_emisor)
    if payload.nit_receptor is not None:
        raw_data["nit_receptor"] = payload.nit_receptor
        txn.nit_receptor = normalize_nit(payload.nit_receptor)
    if payload.items is not None:
        raw_data["items"] = [
            {"descripcion": it.descripcion, "subtotal": it.subtotal, "iva": it.iva}
            for it in payload.items
        ]
        subtotal = sum(it.subtotal for it in payload.items)
        iva = sum(it.iva for it in payload.items)
        raw_data["totales"] = {
            "subtotal": subtotal,
            "iva": iva,
            "total": (
                payload.total if payload.total is not None else float(txn.total or 0)
            ),
        }
    if payload.total is not None:
        if "totales" not in raw_data:
            raw_data["totales"] = {}
        raw_data["totales"]["total"] = payload.total

    # Apply simple field updates
    update_kwargs: dict = {}
    if payload.fecha is not None:
        parsed = safe_datetime(payload.fecha)
        if parsed is None:
            raise HTTPException(
                status_code=422,
                detail=f"La fecha '{payload.fecha}' no pudo ser interpretada. Use el formato YYYY-MM-DD o DD/MM/YYYY.",
            )
        update_kwargs["fecha"] = parsed
        raw_data["fecha"] = payload.fecha
    if payload.concepto is not None:
        update_kwargs["descripcion"] = payload.concepto
    if payload.total is not None:
        update_kwargs["total"] = Decimal(str(payload.total))

    db_service.update_transaction_pending(
        db,
        txn_id=id,
        items=raw_data.get("items", []) if payload.items is not None else None,
        raw_data=(
            raw_data
            if any(
                k in payload.model_dump(exclude_unset=True)
                for k in (
                    "concepto",
                    "total",
                    "items",
                    "tipo_documento",
                    "nit_emisor",
                    "nit_receptor",
                    "fecha",
                )
            )
            else None
        ),
        **update_kwargs,
    )

    db.refresh(txn)
    return {
        "id": txn.id,
        "fecha": str(txn.fecha) if txn.fecha else "",
        "concepto": txn.descripcion or "",
        "total": float(txn.total) if txn.total else 0,
        "status": txn.status.value,
    }


class ReprocessResponse(BaseModel):
    old_transaction_id: str
    new_transaction_id: str
    new_ingest_id: str


@router.post("/{id}/reprocess", status_code=201, response_model=ReprocessResponse)
@limiter.limit("30/minute")
async def reprocess_transaction(
    request: Request,
    id: str,
    payload: Optional[CreateTransactionPayload] = Body(None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a posted transaction and recreate it as pending for re-processing."""
    txn = db.query(TransactionPending).filter(TransactionPending.id == id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transacción {id} no encontrada.")

    if txn.status != TransactionStatus.POSTED:
        raise HTTPException(
            status_code=409,
            detail="Solo las transacciones contabilizadas (POSTED) pueden ser reprocesadas.",
        )

    # Capture data before deletion in case resync commits
    old_raw_data = dict(txn.raw_data or {})
    old_fecha = txn.fecha
    old_descripcion = txn.descripcion
    old_total = txn.total
    old_nit_emisor = txn.nit_emisor
    old_nit_receptor = txn.nit_receptor
    old_items = txn.items

    company_nit = _delete_transaction_cascade(db, id)
    _resync_derived_statements(db, company_nit)

    # Use updated data if provided, otherwise copy from old raw_data
    if payload:
        raw_data = _build_raw_data(payload)
        fecha = safe_datetime(payload.fecha)
        descripcion = payload.concepto
        total = Decimal(str(payload.total))
        nit_emisor = normalize_nit(payload.nit_emisor)
        nit_receptor = normalize_nit(payload.nit_receptor)
        items_data = raw_data["items"]
    else:
        raw_data = old_raw_data
        fecha = old_fecha
        descripcion = old_descripcion
        total = old_total
        nit_emisor = old_nit_emisor
        nit_receptor = old_nit_receptor
        items_data = old_items

    # Create new synthetic ingest
    ingest_job = db_service.create_manual_ingest_job(
        db, company_nit=company_nit or "", created_by=str(current_user.id)
    )

    new_txn = db_service.create_transaction_pending(
        db,
        ingest_id=ingest_job.id,
        fecha=fecha,
        company_nit=company_nit,
        nit_emisor=nit_emisor,
        nit_receptor=nit_receptor,
        total=total,
        descripcion=descripcion,
        items=items_data,
        raw_data=raw_data,
        source_file=None,
        commit=True,
    )

    db.commit()
    return ReprocessResponse(
        old_transaction_id=id,
        new_transaction_id=new_txn.id,
        new_ingest_id=ingest_job.id,
    )


@router.delete("/{id}", status_code=204)
@limiter.limit("30/minute")
async def delete_transaction(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a single transaction (cascade: pending + posted + journal lines).

    The list/detail surface a transaction by its ``TransactionPending`` id, so we
    resolve and delete via the pending id (mirroring ``PATCH /{id}`` and reprocess)
    instead of the posted id. Re-syncs journal-derived statements afterwards so
    reports/derivation stay coherent.

    Note: this is a hard delete (cascade) and cannot be undone.
    """
    company_nit = _delete_transaction_cascade(db, id)  # raises 404 if not found
    db.commit()
    _resync_derived_statements(db, company_nit)


@router.delete("/by-ingest/{ingest_id}", status_code=200)
@limiter.limit("30/minute")
async def delete_transactions_by_ingest(
    request: Request,
    ingest_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all transactions belonging to a specific ingest document (cascade)."""
    from app.models.database import TransactionPending

    txn_ids = [
        row.id
        for row in db.query(TransactionPending.id)
        .filter(TransactionPending.ingest_id == ingest_id)
        .all()
    ]
    # Idempotent: no matching transactions (already deleted, double-click, retry)
    # is a harmless no-op, not a 404.
    if not txn_ids:
        return {"deleted": 0}

    company_nit = None
    for txn_id in txn_ids:
        company_nit = _delete_transaction_cascade(db, txn_id) or company_nit
    db.commit()
    _resync_derived_statements(db, company_nit)

    return {"deleted": len(txn_ids)}


# ─── Manual nota de ajuste contable ──────────────────────────────────────────


class AjusteLineRequest(BaseModel):
    cuenta_puc: str
    tipo_movimiento: str  # "debito" | "credito"
    valor: float
    descripcion: str = ""


class ManualAjusteRequest(BaseModel):
    company_nit: str
    fecha: str  # ISO date string, e.g. "2026-06-13"
    concepto: str
    lines: List[AjusteLineRequest]


class ManualAjusteResponse(BaseModel):
    transaction_id: str
    lines_created: int


@router.post("/manual-ajuste", status_code=201, response_model=ManualAjusteResponse)
@limiter.limit("60/minute")
async def create_manual_ajuste(
    request: Request,
    body: ManualAjusteRequest,
    db: Session = Depends(get_db),
):
    """Persist a CPA-prepared nota de ajuste contable directly to journal_entry_lines.

    Validates double-entry balance (Σdébitos == Σcréditos) and requires at least
    two lines. Skips the LLM pipeline entirely — accounts are trusted as-is.
    """
    from decimal import Decimal as D
    import uuid
    from datetime import datetime, timezone

    from app.models.database import (
        JournalEntryLine,
        TransactionPosted,
        TransactionPending,
        TransactionStatus,
    )
    from app.services.nit_utils import normalize_nit

    # ── validation ──────────────────────────────────────────────────────────
    if len(body.lines) < 2:
        raise HTTPException(
            status_code=422,
            detail="Una nota de ajuste requiere al menos dos líneas contables.",
        )

    total_debito = sum(
        D(str(ln.valor)) for ln in body.lines if ln.tipo_movimiento == "debito"
    )
    total_credito = sum(
        D(str(ln.valor)) for ln in body.lines if ln.tipo_movimiento == "credito"
    )

    if total_debito != total_credito:
        raise HTTPException(
            status_code=422,
            detail=(
                f"El asiento no cuadra: Σdébitos={total_debito} ≠ Σcréditos={total_credito}. "
                "Corrija los valores antes de registrar."
            ),
        )

    # ── parse fecha ─────────────────────────────────────────────────────────
    try:
        fecha_dt = datetime.fromisoformat(body.fecha).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Fecha inválida: '{body.fecha}'. Use formato ISO (YYYY-MM-DD).",
        )

    company_nit_clean = normalize_nit(body.company_nit)
    ajuste_id = str(uuid.uuid4())
    pending_id = str(uuid.uuid4())

    # ── synthetic ingest job ─────────────────────────────────────────────────
    # transactions_pending.ingest_id is NOT NULL (FK → ingest_jobs.id), so a
    # manual ajuste needs a backing ingest job just like create_manual_transaction.
    # commit=False keeps it in this request's transaction (flushed, not committed).
    ingest_job = db_service.create_manual_ingest_job(
        db, company_nit=company_nit_clean, commit=False
    )

    # ── create a synthetic TransactionPending stub (required FK) ─────────────
    pending = TransactionPending(
        id=pending_id,
        ingest_id=ingest_job.id,
        fecha=fecha_dt,
        company_nit=company_nit_clean,
        nit_emisor=company_nit_clean,
        nit_receptor=company_nit_clean,
        total=float(total_debito),
        descripcion=body.concepto,
        status=TransactionStatus.POSTED,
        raw_data={
            "doc_type": "nota_ajuste_contable",
            "concepto": body.concepto,
            "manual_entry": True,
        },
    )
    db.add(pending)

    # ── create TransactionPosted ─────────────────────────────────────────────
    # Use first debit account as the primary PUC for the posted record.
    primary_cuenta = next(
        (ln.cuenta_puc for ln in body.lines if ln.tipo_movimiento == "debito"),
        body.lines[0].cuenta_puc,
    )
    posted = TransactionPosted(
        id=ajuste_id,
        transaction_pending_id=pending_id,
        company_nit=company_nit_clean,
        cuenta_puc=primary_cuenta,
        puc_descripcion=body.concepto,
        retefuente=D("0"),
        reteica=D("0"),
        iva=D("0"),
        ica=D("0"),
        provision_renta=D("0"),
        neto_a_pagar=total_debito,
        status=TransactionStatus.POSTED,
        journal_entries_json=[
            {
                "cuenta_puc": ln.cuenta_puc,
                "tipo_movimiento": ln.tipo_movimiento,
                "valor": ln.valor,
                "descripcion": ln.descripcion,
            }
            for ln in body.lines
        ],
    )
    db.add(posted)

    # ── create JournalEntryLines ─────────────────────────────────────────────
    for ln in body.lines:
        debito_val = D(str(ln.valor)) if ln.tipo_movimiento == "debito" else D("0")
        credito_val = D(str(ln.valor)) if ln.tipo_movimiento == "credito" else D("0")
        jel = JournalEntryLine(
            transaction_posted_id=ajuste_id,
            fecha=fecha_dt,
            company_nit=company_nit_clean,
            comprobante=f"NA-{ajuste_id[:8].upper()}",
            cuenta_puc=ln.cuenta_puc,
            descripcion=ln.descripcion or body.concepto,
            debito=debito_val,
            credito=credito_val,
        )
        db.add(jel)

    db.commit()

    logger.info(
        "manual-ajuste: created transaction %s with %d lines for NIT %s",
        ajuste_id,
        len(body.lines),
        company_nit_clean,
    )

    return ManualAjusteResponse(
        transaction_id=ajuste_id,
        lines_created=len(body.lines),
    )
