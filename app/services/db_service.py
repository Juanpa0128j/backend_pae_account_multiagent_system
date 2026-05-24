"""
Database service layer — repository pattern.
All DB operations used by agents, APIs, and the seed script go through here.
"""

# type: ignore[assignment]
# SQLAlchemy Column assignments are safe at runtime; Pylance flags them incorrectly.

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import distinct, extract, func
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models.database import (
    AuditLog,
    CompanySettings,
    CuentaPUC,
    FinancialStatement,
    FinancialStatementLineage,
    IngestJob,
    IngestStatus,
    JournalEntryLine,
    ProcessJob,
    ProcessStatus,
    ReteicaTarifa,
    TaxBaseMinima,
    Tercero,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
    UserCompany,
    UvtValue,
)

logger = get_logger(__name__)


def _generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    ts = int(datetime.now(timezone.utc).timestamp())
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}{ts}_{short_uuid}" if prefix else f"{ts}_{short_uuid}"


def _commit_or_flush(db: Session, commit: bool) -> None:
    """Commit the transaction or flush pending changes without committing.

    Pass ``commit=False`` when the caller manages the transaction boundary
    itself (e.g. db_persist_node wraps the whole pipeline in one transaction).
    """
    if commit:
        db.commit()
    else:
        db.flush()


# ─── IngestJob ───────────────────────────────────────────────────


def create_ingest_job(
    db: Session,
    file_name: str,
    file_path: str = None,
    company_nit: Optional[str] = None,
    document_type: Optional[str] = None,
    pathway: Optional[str] = None,
    classification_confirmed: Optional[bool] = None,
    parser_mode: str = "fast",
    commit: bool = True,
    created_by: str | None = None,
    file_names: list[str] | None = None,
    multi_file_mode: str = "pages",
) -> IngestJob:
    """Create a new ingest job for a document upload."""
    job = IngestJob(
        id=_generate_id("ing_"),
        file_name=file_name,
        file_path=file_path,
        file_names=file_names,
        multi_file_mode=multi_file_mode,
        status=IngestStatus.PENDING_PROCESSING,
        company_nit=company_nit or None,
        document_type=document_type or None,
        pathway=pathway or None,
        classification_confirmed=classification_confirmed,
        parser_mode=parser_mode,
    )
    db.add(job)
    # Stage audit log before the single commit/flush so job + log are atomic
    create_audit_log(
        db,
        "ingest_created",
        job.id,
        "ingest",
        {"file_name": file_name},
        commit=False,
        created_by=created_by,
    )
    _commit_or_flush(db, commit)
    db.refresh(job)
    logger.info(f"Created IngestJob: {job.id}")
    return job


def update_ingest_file_index(db: Session, ingest_id: str, index: int) -> None:
    """Update current_file_index on IngestJob for frontend progress reporting."""
    db.query(IngestJob).filter(IngestJob.id == ingest_id).update(
        {"current_file_index": index}
    )
    db.commit()


def update_ingest_job(
    db: Session,
    ingest_id: str,
    status: IngestStatus,
    raw_preview: Dict = None,
    extraction_errors: List[str] = None,
    document_type: str = None,
    pathway: str = None,
    classification_confirmed: Optional[bool] = None,
    classification_confidence: Optional[Decimal] = None,
    commit: bool = True,
) -> Optional[IngestJob]:
    """Update an ingest job's status and preview data."""
    job = db.query(IngestJob).filter(IngestJob.id == ingest_id).first()
    if not job:
        return None

    job.status = status
    if raw_preview is not None:
        job.raw_preview = raw_preview
    if extraction_errors is not None:
        job.extraction_errors = extraction_errors
    if document_type is not None:
        job.document_type = document_type
    if pathway is not None:
        job.pathway = pathway
    if classification_confirmed is not None:
        job.classification_confirmed = classification_confirmed
    if classification_confidence is not None:
        job.classification_confidence = classification_confidence
    if status in (IngestStatus.COMPLETED, IngestStatus.FAILED):
        job.completed_at = datetime.now(timezone.utc)

    _commit_or_flush(db, commit)
    db.refresh(job)
    return job


def get_ingest_job(db: Session, ingest_id: str) -> Optional[IngestJob]:
    """Get an ingest job by ID."""
    return db.query(IngestJob).filter(IngestJob.id == ingest_id).first()


# ─── TransactionPending ─────────────────────────────────────────


def create_transaction_pending(
    db: Session,
    ingest_id: str,
    fecha: Optional[datetime] = None,
    nit_emisor: Optional[str] = None,
    nit_receptor: Optional[str] = None,
    total: Optional[Decimal] = None,
    descripcion: Optional[str] = None,
    items: Optional[List[Dict]] = None,
    raw_data: Optional[Dict] = None,
    company_nit: Optional[str] = None,
    commit: bool = True,
    created_by: str | None = None,
    source_file: Optional[str] = None,
) -> TransactionPending:
    """Create a pending transaction from extracted data."""
    txn = TransactionPending(
        id=_generate_id("txn_"),
        ingest_id=ingest_id,
        company_nit=company_nit,
        fecha=fecha,
        nit_emisor=nit_emisor,
        nit_receptor=nit_receptor,
        total=total,
        descripcion=descripcion,
        items=items,
        raw_data=raw_data,
        source_file=source_file,
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    # Stage audit log before the single commit/flush so txn + log are atomic
    create_audit_log(
        db,
        "transaction_pending_created",
        txn.id,
        "transaction",
        {
            "ingest_id": ingest_id,
            "total": str(total) if total else None,
        },
        commit=False,
        company_nit=company_nit,
        created_by=created_by,
    )
    _commit_or_flush(db, commit)
    db.refresh(txn)
    return txn


def get_transactions_by_ingest(db: Session, ingest_id: str) -> List[TransactionPending]:
    """Get all pending transactions for an ingest job."""
    return (
        db.query(TransactionPending)
        .filter(TransactionPending.ingest_id == ingest_id)
        .all()
    )


def get_transactions_by_status(
    db: Session,
    status: TransactionStatus = None,
    limit: int = 50,
    offset: int = 0,
    company_nit: str = None,
) -> List[TransactionPending]:
    """Get transactions optionally filtered by status and company NIT."""
    query = db.query(TransactionPending)
    if status:
        query = query.filter(TransactionPending.status == status)
    if company_nit:
        query = query.filter(TransactionPending.company_nit == company_nit)
    return (
        query.order_by(TransactionPending.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_transactions_by_nit(
    db: Session, nit: str, limit: int = 10
) -> List[TransactionPosted]:
    """Get posted transactions by NIT emisor (for agent historical lookup)."""
    return (
        db.query(TransactionPosted)
        .join(TransactionPending)
        .filter(TransactionPending.nit_emisor == nit)
        .order_by(TransactionPosted.created_at.desc())
        .limit(limit)
        .all()
    )


def update_transaction_status(
    db: Session, txn_id: str, status: TransactionStatus, commit: bool = True
) -> Optional[TransactionPending]:
    """Update a pending transaction's status."""
    txn = db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
    if txn:
        txn.status = status
        _commit_or_flush(db, commit)
        db.refresh(txn)
    return txn


# ─── TransactionPosted ──────────────────────────────────────────


def create_transaction_posted(
    db: Session,
    transaction_pending_id: str,
    cuenta_puc: str,
    puc_descripcion: Optional[str] = None,
    retefuente: Decimal = Decimal("0"),
    reteica: Decimal = Decimal("0"),
    iva: Decimal = Decimal("0"),
    ica: Decimal = Decimal("0"),
    provision_renta: Decimal = Decimal("0"),
    neto_a_pagar: Decimal = Decimal("0"),
    journal_entries_json: Optional[List[Dict]] = None,
    tax_references: Optional[List[str]] = None,
    agent_reasoning: Optional[Dict] = None,
    company_nit: Optional[str] = None,
    commit: bool = True,
    created_by: str | None = None,
) -> TransactionPosted:
    """Create a fully processed posted transaction."""
    posted = TransactionPosted(
        id=_generate_id("posted_"),
        transaction_pending_id=transaction_pending_id,
        company_nit=company_nit,
        cuenta_puc=cuenta_puc,
        puc_descripcion=puc_descripcion,
        retefuente=retefuente,
        reteica=reteica,
        iva=iva,
        ica=ica,
        provision_renta=provision_renta,
        neto_a_pagar=neto_a_pagar,
        journal_entries_json=journal_entries_json,
        tax_references=tax_references,
        agent_reasoning=agent_reasoning,
        status=TransactionStatus.POSTED,
    )
    db.add(posted)

    # Also update the pending transaction status
    pending = (
        db.query(TransactionPending)
        .filter(TransactionPending.id == transaction_pending_id)
        .first()
    )
    if pending:
        pending.status = TransactionStatus.POSTED

    # Stage audit log before the single commit/flush so posted + log are atomic
    create_audit_log(
        db,
        "transaction_posted",
        posted.id,
        "transaction",
        {
            "cuenta_puc": cuenta_puc,
            "pending_id": transaction_pending_id,
        },
        commit=False,
        company_nit=company_nit,
        created_by=created_by,
    )
    _commit_or_flush(db, commit)
    db.refresh(posted)
    return posted


# ─── JournalEntryLine ───────────────────────────────────────────


def _parse_fecha(value) -> datetime:
    """Ensure fecha is a timezone-aware datetime, parsing strings if needed."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def create_journal_entry_lines(
    db: Session,
    transaction_posted_id: str,
    entries: List[Dict[str, Any]],
    commit: bool = True,
    company_nit: Optional[str] = None,
) -> List[JournalEntryLine]:
    """Create normalized journal entry lines for a posted transaction."""
    lines = []
    for entry in entries:
        line = JournalEntryLine(
            transaction_posted_id=transaction_posted_id,
            fecha=_parse_fecha(entry.get("fecha", datetime.now(timezone.utc))),
            company_nit=company_nit,
            comprobante=entry.get("comprobante"),
            cuenta_puc=entry["cuenta"],
            cuenta_nombre=entry.get("descripcion", ""),
            tercero_nit=entry.get("tercero_nit"),
            descripcion=entry.get("detalle", ""),
            debito=Decimal(str(entry.get("debito", 0))),
            credito=Decimal(str(entry.get("credito", 0))),
        )
        lines.append(line)

    db.add_all(lines)
    _commit_or_flush(db, commit)

    for line in lines:
        db.refresh(line)

    return lines


# ─── Accounting Books ───────────────────────────────────────────


def get_daily_journal(
    db: Session,
    start_date: datetime = None,
    end_date: datetime = None,
    company_nit: str = None,
) -> List[JournalEntryLine]:
    """Daily Journal — all journal entries in chronological order (posted transactions only)."""
    query = (
        db.query(JournalEntryLine)
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
    )
    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)
    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)
    return query.order_by(JournalEntryLine.fecha, JournalEntryLine.comprobante).all()


def get_general_ledger(
    db: Session,
    start_date: datetime = None,
    end_date: datetime = None,
    company_nit: str = None,
) -> List[Dict]:
    """
    Libro Mayor — aggregated by cuenta_puc (posted transactions only).
    Returns list of dicts with: cuenta, nombre, saldo_debito, saldo_credito, saldo_neto
    """
    # LEFT JOIN con CuentaPUC para hidratar el nombre desde el catálogo cuando
    # `JournalEntryLine.cuenta_nombre` viene vacío (contador algunas veces no
    # pobla el campo al persistir). COALESCE: catálogo > MAX(journal) > vacío.
    # MAX() en cuenta_nombre journal es agregación que satisface GROUP BY
    # (necesario porque cuenta_nombre journal NO está en GROUP BY — ver Bug U).
    name_expr = func.coalesce(
        CuentaPUC.nombre,
        func.max(JournalEntryLine.cuenta_nombre),
        "",
    ).label("cuenta_nombre")

    query = (
        db.query(
            JournalEntryLine.cuenta_puc,
            name_expr,
            func.sum(JournalEntryLine.debito).label("total_debit"),
            func.sum(JournalEntryLine.credito).label("total_credit"),
        )
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .outerjoin(CuentaPUC, CuentaPUC.codigo == JournalEntryLine.cuenta_puc)
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
        # NOTA: NO incluir `JournalEntryLine.cuenta_nombre` en el GROUP BY.
        # Si una cuenta tiene lines con cuenta_nombre vacío y otras con texto,
        # GROUP BY los separaría en 2 filas (Bug U). Agrupamos solo por
        # cuenta_puc + CuentaPUC.nombre (1-a-1 con puc) y el SELECT usa
        # COALESCE para resolver el nombre display (catálogo prioritario).
        .group_by(
            JournalEntryLine.cuenta_puc,
            CuentaPUC.nombre,
        )
    )

    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)
    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)

    results = query.order_by(JournalEntryLine.cuenta_puc).all()

    return [
        {
            "account": r.cuenta_puc,
            "name": r.cuenta_nombre or "",
            "total_debit": float(r.total_debit or 0),
            "total_credit": float(r.total_credit or 0),
            "net_balance": float((r.total_debit or 0) - (r.total_credit or 0)),
        }
        for r in results
    ]


def get_subsidiary_journal(
    db: Session,
    account: str,
    start_date: datetime = None,
    end_date: datetime = None,
    company_nit: str = None,
) -> List[JournalEntryLine]:
    """Subsidiary journal — detail for a specific account (posted transactions only)."""
    query = (
        db.query(JournalEntryLine)
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
        .filter(JournalEntryLine.cuenta_puc == account)
    )
    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)
    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)
    return query.order_by(JournalEntryLine.fecha).all()


def get_balance_sheet(
    db: Session, cutoff_date: datetime = None, company_nit: str = None
) -> Dict:
    """
    Balance Sheet (Statement of Financial Position, posted transactions only).
    Assets (class 1) = Liabilities (class 2) + Equity (class 3)

    Revenue (4) and Expenses (5,6) flow into retained earnings.

    The PUC has natural-balance exceptions: some accounts live in class 2
    (Pasivos) but carry a DEBIT natural balance (e.g. 240802 IVA descontable
    is an activo recuperable even though grouped under "Impuestos por pagar").
    We honour each account's ``naturaleza`` from ``cuentas_puc`` so those
    auxiliaries land on the correct side of the balance sheet instead of
    deflating their parent class total.
    """
    query = (
        db.query(JournalEntryLine)
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
    )
    if cutoff_date:
        query = query.filter(JournalEntryLine.fecha <= cutoff_date)
    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)

    lines = query.all()

    # Look up each cuenta_puc's natural balance from the catalog so we can
    # reclassify the few PUC accounts that contradict their first-digit class.
    distinct_codes = {ln.cuenta_puc for ln in lines if ln.cuenta_puc}
    naturaleza_by_code: Dict[str, str] = {}
    if distinct_codes:
        # Pull the exact codes plus their 6-digit and 4-digit parents so the
        # fallback below can resolve naturaleza for auxiliary sub-accounts that
        # are not seeded individually (e.g. 240802 ↔ parent 2408).
        candidate_codes: set[str] = set()
        for code in distinct_codes:
            candidate_codes.add(code)
            if len(code) >= 6:
                candidate_codes.add(code[:6])
            if len(code) >= 4:
                candidate_codes.add(code[:4])
        catalog_rows = (
            db.query(CuentaPUC.codigo, CuentaPUC.naturaleza)
            .filter(CuentaPUC.codigo.in_(candidate_codes))
            .all()
        )
        for codigo, naturaleza in catalog_rows:
            naturaleza_by_code[str(codigo)] = (
                naturaleza.value if hasattr(naturaleza, "value") else str(naturaleza)
            )

    def _presentation_class(code: str, raw_clase: int) -> int:
        """Return the class digit the account should ROLL UP to on the BS.

        Honours debit-nature auxiliaries housed inside class 2 / 3 (move to 1)
        and credit-nature auxiliaries inside class 1 (move to 2). Defaults to
        the original class when no catalog entry exists.
        """
        nat = (
            naturaleza_by_code.get(code)
            or (len(code) >= 6 and naturaleza_by_code.get(code[:6]))
            or (len(code) >= 4 and naturaleza_by_code.get(code[:4]))
        )
        if not nat:
            return raw_clase
        nat_upper = str(nat).upper()
        if nat_upper.startswith("D") and raw_clase in (2, 3):
            return 1
        if nat_upper.startswith("C") and raw_clase == 1:
            return 2
        return raw_clase

    totals = {
        1: Decimal("0"),
        2: Decimal("0"),
        3: Decimal("0"),
        4: Decimal("0"),
        5: Decimal("0"),
        6: Decimal("0"),
    }

    for line in lines:
        if not line.cuenta_puc or not line.cuenta_puc[0].isdigit():
            continue
        raw_clase = int(line.cuenta_puc[0])
        if raw_clase not in totals:
            continue
        clase = _presentation_class(line.cuenta_puc, raw_clase)
        if clase in (1, 5, 6):
            totals[clase] += (line.debito or Decimal("0")) - (
                line.credito or Decimal("0")
            )
        else:
            totals[clase] += (line.credito or Decimal("0")) - (
                line.debito or Decimal("0")
            )

    # Retained earnings = Revenue - Expenses - Cost of Sales
    net_profit = totals[4] - totals[5] - totals[6]

    # Tolerancia $1 — DIAN facturas + Decimal rounding suelen dejar diferencias
    # de centavos (e.g. activos=$9,937,909.24 vs P+E=$9,937,909.24 con diff
    # exact $0.00 pero strict Decimal == puede fallar por trailing zeros).
    # Mismo `_BALANCE_TOLERANCE = $1.00` que Fix G/H en auditores y journal_builder.
    diff = abs(totals[1] - (totals[2] + totals[3] + net_profit))
    is_balanced = diff <= Decimal("1.00")

    return {
        "assets": float(totals[1]),
        "liabilities": float(totals[2]),
        "equity": float(totals[3]),
        "revenue": float(totals[4]),
        "expenses": float(totals[5]),
        "cost_of_sales": float(totals[6]),
        "net_profit": float(net_profit),
        "total_equity": float(totals[3] + net_profit),
        "is_balanced": is_balanced,
    }


# ─── Search & Duplicate Detection ───────────────────────────────


def search_transactions(
    db: Session,
    nit: str = None,
    fecha_inicio: datetime = None,
    fecha_fin: datetime = None,
    status: TransactionStatus = None,
    limit: int = 50,
) -> List[TransactionPending]:
    """Search transactions with multiple filters."""
    query = db.query(TransactionPending)
    if nit:
        query = query.filter(
            (TransactionPending.nit_emisor == nit)
            | (TransactionPending.nit_receptor == nit)
        )
    if fecha_inicio:
        query = query.filter(TransactionPending.fecha >= fecha_inicio)
    if fecha_fin:
        query = query.filter(TransactionPending.fecha <= fecha_fin)
    if status:
        query = query.filter(TransactionPending.status == status)
    return query.order_by(TransactionPending.created_at.desc()).limit(limit).all()


def check_duplicates(
    db: Session,
    issuer_nit: str,
    total: Decimal,
    date: datetime,
    days_window: int = 3,
) -> List[TransactionPending]:
    """Check for potential duplicate transactions (same NIT, amount, date ±N days)."""
    start_date = date - timedelta(days=days_window)
    end_date = date + timedelta(days=days_window)

    return (
        db.query(TransactionPending)
        .filter(
            TransactionPending.nit_emisor == issuer_nit,
            TransactionPending.total == total,
            TransactionPending.fecha >= start_date,
            TransactionPending.fecha <= end_date,
        )
        .all()
    )


# ─── PUC ─────────────────────────────────────────────────────────


def validate_puc_exists(db: Session, codigo: str) -> Optional[CuentaPUC]:
    """Validate a PUC code exists and is active."""
    return (
        db.query(CuentaPUC).filter(CuentaPUC.codigo == codigo, CuentaPUC.activa).first()
    )


def get_all_puc(db: Session) -> List[CuentaPUC]:
    """Get all active PUC accounts."""
    return (
        db.query(CuentaPUC)
        .filter(CuentaPUC.activa == True)  # noqa: E712
        .order_by(CuentaPUC.codigo)
        .all()
    )


def search_puc(
    db: Session, search_term: str, limit: int = 10, include_inactive: bool = False
) -> List[CuentaPUC]:
    """Search PUC accounts by code or name. Optionally include inactive accounts."""
    query = db.query(CuentaPUC).filter(
        (CuentaPUC.codigo.ilike(f"%{search_term}%"))
        | (CuentaPUC.nombre.ilike(f"%{search_term}%"))
    )
    if not include_inactive:
        query = query.filter(CuentaPUC.activa)
    return query.limit(limit).all()


def create_puc(db: Session, data: dict, commit: bool = True) -> CuentaPUC:
    """Create new PUC account. Raises ValueError if codigo already exists."""
    from sqlalchemy.exc import IntegrityError

    row = CuentaPUC(**data)
    db.add(row)
    try:
        _commit_or_flush(db, commit)
        db.refresh(row)
        return row
    except IntegrityError as e:
        db.rollback()
        if "codigo" in str(e):
            raise ValueError(f"PUC code {data.get('codigo')} already exists")
        raise


def update_puc(
    db: Session, codigo: str, data: dict, commit: bool = True
) -> Optional[CuentaPUC]:
    """Update existing PUC account. Returns None if not found."""
    row = db.query(CuentaPUC).filter(CuentaPUC.codigo == codigo).first()
    if not row:
        return None
    for key, value in data.items():
        if key != "codigo":
            setattr(row, key, value)
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row


def get_all_puc_including_inactive(db: Session) -> List[CuentaPUC]:
    """Get ALL PUC accounts (active + inactive) ordered by codigo."""
    return db.query(CuentaPUC).order_by(CuentaPUC.codigo).all()


# ─── ProcessJob ──────────────────────────────────────────────────


def create_process_job(
    db: Session, ingest_id: str, commit: bool = True, created_by: str | None = None
) -> ProcessJob:
    """Create a new processing job."""
    job = ProcessJob(
        id=_generate_id("proc_"),
        ingest_id=ingest_id,
        status=ProcessStatus.QUEUED,
        agent_log=[],
    )
    db.add(job)
    # Stage audit log before the single commit/flush so job + log are atomic
    create_audit_log(
        db,
        "process_created",
        job.id,
        "process",
        {"ingest_id": ingest_id},
        commit=False,
        created_by=created_by,
    )
    _commit_or_flush(db, commit)
    db.refresh(job)
    return job


def update_process_job(
    db: Session,
    process_id: str,
    status: ProcessStatus = None,
    current_stage: str = None,
    current_agent: str = None,
    progress: int = None,
    error_message: str = None,
    agent_log_entry: Dict = None,
) -> Optional[ProcessJob]:
    """Update a process job's status and progress."""
    job = db.query(ProcessJob).filter(ProcessJob.id == process_id).first()
    if not job:
        return None

    if status is not None:
        job.status = status
        if status == ProcessStatus.RUNNING and not job.started_at:
            job.started_at = datetime.now(timezone.utc)
        if status in (ProcessStatus.COMPLETED, ProcessStatus.FAILED):
            job.completed_at = datetime.now(timezone.utc)

    if current_stage is not None:
        job.current_stage = current_stage
    if current_agent is not None:
        job.current_agent = current_agent
    if progress is not None:
        job.progress = progress
    if error_message is not None:
        job.error_message = error_message
    if agent_log_entry:
        existing = job.agent_log if isinstance(job.agent_log, list) else []
        job.agent_log = existing + [agent_log_entry]

    db.commit()
    db.refresh(job)
    return job


def get_process_job(db: Session, process_id: str) -> Optional[ProcessJob]:
    """Get a process job by ID."""
    return db.query(ProcessJob).filter(ProcessJob.id == process_id).first()


def get_active_process_job_for_ingest(
    db: Session, ingest_id: str
) -> Optional[ProcessJob]:
    """
    Get an active (non-failed) ProcessJob for the given ingest_id.

    Returns the latest ProcessJob that is not FAILED or CANCELLED.
    This prevents duplicate processing of the same ingest job.
    """
    return (
        db.query(ProcessJob)
        .filter(
            ProcessJob.ingest_id == ingest_id,
            ProcessJob.status.in_(
                [
                    ProcessStatus.QUEUED,
                    ProcessStatus.RUNNING,
                    ProcessStatus.COMPLETED,
                    ProcessStatus.PENDING_AUDIT_REVIEW,
                ]
            ),
        )
        .order_by(ProcessJob.created_at.desc())
        .first()
    )


def get_process_result_transactions(
    db: Session, ingest_id: str
) -> List[Dict[str, Any]]:
    """Get final posted transaction payload for a given ingest job."""
    rows = (
        db.query(TransactionPending, TransactionPosted)
        .join(
            TransactionPosted,
            TransactionPosted.transaction_pending_id == TransactionPending.id,
        )
        .filter(TransactionPending.ingest_id == ingest_id)
        .all()
    )

    result: List[Dict[str, Any]] = []
    for pending, posted in rows:
        result.append(
            {
                "transaction_pending_id": pending.id,
                "transaction_posted_id": posted.id,
                "date": pending.fecha.isoformat() if pending.fecha else None,
                "company_nit": pending.company_nit,
                "issuer_nit": pending.nit_emisor,
                "receiver_nit": pending.nit_receptor,
                "description": pending.descripcion,
                "total": float(pending.total) if pending.total is not None else None,
                "puc_account": posted.cuenta_puc,
                "puc_description": posted.puc_descripcion,
                "withholding_tax": float(posted.retefuente or 0),
                "ica_tax": float(posted.reteica or 0),
                "vat": float(posted.iva or 0),
                "net_amount_due": float(posted.neto_a_pagar or 0),
                "journal_entries": posted.journal_entries_json or [],
                "tax_references": posted.tax_references or [],
                "agent_reasoning": posted.agent_reasoning or {},
            }
        )

    return result


# ─── AuditLog ────────────────────────────────────────────────────


def create_audit_log(
    db: Session,
    action: str,
    entity_id: str = None,
    entity_type: str = None,
    details: Dict = None,
    commit: bool = True,
    company_nit: str | None = None,
    created_by: str | None = None,
) -> AuditLog:
    """Create an immutable audit log entry."""
    log = AuditLog(
        action=action,
        entity_id=entity_id,
        entity_type=entity_type,
        company_nit=company_nit,
        details=details,
        created_by=created_by,
    )
    db.add(log)
    _commit_or_flush(db, commit)
    return log


# ─── Financial Statements (Vía B + derived) ───────────────────────────────


def create_financial_statement(
    db: Session,
    *,
    ingest_id: str,
    statement_type: str,
    data: Dict[str, Any],
    entity_nit: str = None,
    period_start: datetime = None,
    period_end: datetime = None,
    source_mode: str = "direct",
    commit: bool = True,
) -> FinancialStatement:
    row = FinancialStatement(
        id=_generate_id("fs_"),
        ingest_id=ingest_id,
        statement_type=statement_type,
        period_start=period_start,
        period_end=period_end,
        entity_nit=entity_nit,
        source_mode=source_mode,
        data=data,
    )
    db.add(row)
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row


def create_financial_statement_lineage(
    db: Session,
    *,
    target_statement_id: str,
    source_statement_id: str,
    relation_type: str = "input",
    commit: bool = True,
) -> FinancialStatementLineage:
    row = FinancialStatementLineage(
        id=_generate_id("fsl_"),
        target_statement_id=target_statement_id,
        source_statement_id=source_statement_id,
        relation_type=relation_type,
    )
    db.add(row)
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row


def get_financial_statements(
    db: Session,
    *,
    company_nit: str,
    statement_type: str | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    source_mode: str | None = None,
) -> List[FinancialStatement]:
    query = db.query(FinancialStatement).filter(
        FinancialStatement.entity_nit == company_nit
    )

    if statement_type:
        query = query.filter(FinancialStatement.statement_type == statement_type)
    if source_mode:
        query = query.filter(FinancialStatement.source_mode == source_mode)
    if period_start:
        query = query.filter(FinancialStatement.period_start >= period_start)
    if period_end:
        query = query.filter(FinancialStatement.period_end <= period_end)

    return query.order_by(
        FinancialStatement.period_end.desc(), FinancialStatement.created_at.desc()
    ).all()


# ─── Terceros ────────────────────────────────────────────────────


def get_or_create_third_party(
    db: Session,
    nit: str,
    business_name: str = "Unknown",
    party_type: str = "proveedor",
    commit: bool = True,
) -> Tercero:
    """Get existing third party by NIT or create a new one."""
    tercero = db.query(Tercero).filter(Tercero.nit == nit).first()
    if not tercero:
        from app.models.database import TerceroTipo

        tercero = Tercero(
            nit=nit,
            razon_social=business_name,
            tipo=TerceroTipo(party_type),
        )
        db.add(tercero)
        _commit_or_flush(db, commit)
        db.refresh(tercero)
    return tercero


# ─── Company Settings ─────────────────────────────────────────────────────────


def get_company_settings(db: Session, nit: str) -> Optional[CompanySettings]:
    """Return the CompanySettings row for the given NIT, or None if not found."""
    return db.query(CompanySettings).filter(CompanySettings.nit == nit).first()


def get_company_locked_pathway(db: Session, nit: str) -> Optional[str]:
    """Return the locked_pathway for the given NIT, or None if not set/found."""
    row = (
        db.query(CompanySettings.locked_pathway)
        .filter(CompanySettings.nit == nit)
        .first()
    )
    return row[0] if row else None


def get_cuenta_ica_propio(db: Session, nit: str) -> str:
    """Return the configured ICA liability account for the given NIT.

    Falls back to the national default ``2368`` if no custom account is set.
    """
    row = (
        db.query(CompanySettings.cuenta_ica_propio)
        .filter(CompanySettings.nit == nit)
        .first()
    )
    return row[0] if row and row[0] else "2368"


def set_company_locked_pathway(db: Session, nit: str, pathway: str) -> None:
    """Set locked_pathway on first upload — atomic so concurrent first uploads
    can't race and pick different pathways. The conditional UPDATE only
    succeeds when the column is still NULL; subsequent callers are a no-op.
    """
    rows = (
        db.query(CompanySettings)
        .filter(
            CompanySettings.nit == nit,
            CompanySettings.locked_pathway.is_(None),
        )
        .update(
            {CompanySettings.locked_pathway: pathway},
            synchronize_session=False,
        )
    )
    if rows:
        db.commit()


def list_companies(db: Session) -> list[CompanySettings]:
    """Return all CompanySettings rows ordered by NIT."""
    return db.query(CompanySettings).order_by(CompanySettings.nit).all()


def delete_company(db: Session, nit: str) -> bool:
    """Delete the CompanySettings row for the given NIT. Returns True if deleted."""
    row = db.query(CompanySettings).filter(CompanySettings.nit == nit).first()
    if not row:
        return False
    db.query(UserCompany).filter(UserCompany.company_nit == nit).delete(
        synchronize_session=False
    )
    db.delete(row)
    db.commit()
    return True


def upsert_company_settings(
    db: Session, nit: str, data: dict, commit: bool = True
) -> CompanySettings:
    """Create or fully replace the CompanySettings row for the given NIT."""
    row = db.query(CompanySettings).filter(CompanySettings.nit == nit).first()
    if row:
        for key, value in data.items():
            setattr(row, key, value)
    else:
        row = CompanySettings(nit=nit, **data)
        db.add(row)
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row


# ─── ReteICA Tarifa Lookup ────────────────────────────────────────────────────

# Maps CIIU code prefixes to ISIC/CIIU section letters.
# This covers the most common sections used in Colombia.
_CIIU_SECTION_MAP: dict[str, str] = {
    "01": "A",
    "02": "A",
    "03": "A",  # Agricultura, ganadería
    "05": "B",
    "06": "B",
    "07": "B",
    "08": "B",  # Minería
    "10": "C",
    "11": "C",
    "12": "C",
    "13": "C",  # Industria manufacturera
    "14": "C",
    "15": "C",
    "16": "C",
    "17": "C",
    "18": "C",
    "19": "C",
    "20": "C",
    "21": "C",
    "22": "C",
    "23": "C",
    "24": "C",
    "25": "C",
    "26": "C",
    "27": "C",
    "28": "C",
    "29": "C",
    "30": "C",
    "31": "C",
    "32": "C",
    "33": "C",
    "35": "D",  # Electricidad, gas
    "36": "E",
    "37": "E",
    "38": "E",
    "39": "E",  # Agua y saneamiento
    "41": "F",
    "42": "F",
    "43": "F",  # Construcción
    "45": "G",
    "46": "G",
    "47": "G",  # Comercio
    "49": "H",
    "50": "H",
    "51": "H",
    "52": "H",  # Transporte
    "53": "H",
    "55": "I",
    "56": "I",  # Alojamiento, restaurantes
    "58": "J",
    "59": "J",
    "60": "J",
    "61": "J",  # Información, tecnología
    "62": "J",
    "63": "J",
    "64": "K",
    "65": "K",
    "66": "K",  # Financiero, seguros
    "68": "L",  # Inmobiliario
    "69": "M",
    "70": "M",
    "71": "M",
    "72": "M",  # Profesional, científico
    "73": "M",
    "74": "M",
    "75": "M",
    "77": "N",
    "78": "N",
    "79": "N",
    "80": "N",  # Servicios administrativos
    "81": "N",
    "82": "N",
    "84": "O",  # Administración pública
    "85": "P",  # Educación
    "86": "Q",
    "87": "Q",
    "88": "Q",  # Salud
    "90": "R",
    "91": "R",
    "92": "R",
    "93": "R",  # Entretenimiento
    "94": "S",
    "95": "S",
    "96": "S",  # Otras actividades de servicios
    "97": "T",  # Hogares
}


def _normalize_municipio(ciudad: str) -> str:
    """Normalize a city name for DB lookup (lowercase, remove accents)."""
    import unicodedata

    normalized = unicodedata.normalize("NFD", ciudad.lower().strip())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def _ciiu_to_section(ciiu: str) -> str:
    """Map a CIIU code to its ISIC section letter."""
    prefix = ciiu.strip()[:2]
    return _CIIU_SECTION_MAP.get(prefix, "general")


def get_reteica_tarifa(db: Session, ciudad: str, ciiu: str) -> Optional[float]:
    """
    Look up the ReteICA rate for a given city and CIIU code.

    Lookup priority:
      1. municipio + ciiu_seccion (exact city + sector)
      2. municipio + 'general'    (city-wide default)
      3. 'general' + 'general'    (national fallback)

    Returns the rate as a float (decimal fraction), or None if no entry found.
    """
    municipio = _normalize_municipio(ciudad)
    seccion = _ciiu_to_section(ciiu)

    # 1. Exact city + sector
    if seccion != "general":
        row = (
            db.query(ReteicaTarifa)
            .filter(
                ReteicaTarifa.municipio == municipio,
                ReteicaTarifa.ciiu_seccion == seccion,
            )
            .first()
        )
        if row:
            return float(row.tasa)

    # 2. City general
    row = (
        db.query(ReteicaTarifa)
        .filter(
            ReteicaTarifa.municipio == municipio,
            ReteicaTarifa.ciiu_seccion == "general",
        )
        .first()
    )
    if row:
        return float(row.tasa)

    # 3. National fallback
    row = (
        db.query(ReteicaTarifa)
        .filter(
            ReteicaTarifa.municipio == "general",
            ReteicaTarifa.ciiu_seccion == "general",
        )
        .first()
    )
    if row:
        return float(row.tasa)

    return None


# ─── VectorDocument ───────────────────────────────────────────────────────────


def count_vector_documents(db: Session, collection_name: str) -> int:
    """Return document count for a collection (health/readiness check)."""
    from sqlalchemy import text as _text  # noqa: PLC0415

    result = db.execute(
        _text("SELECT COUNT(*) FROM vector_documents WHERE collection_name = :c"),
        {"c": collection_name},
    ).scalar()
    return int(result or 0)


# ─── Financial Statement Helpers ──────────────────────────────────────────────


def financial_statements_exist(
    db: Session,
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
    types: list[str],
) -> bool:
    """Return True if all requested statement types exist for this company and period window.

    Checks that statements match BOTH period_start and period_end to exclude cross-period
    overlaps that might appear in overlapping but different fiscal periods.
    """
    count = (
        db.query(func.count(distinct(FinancialStatement.statement_type)))
        .filter(FinancialStatement.entity_nit == company_nit)
        .filter(FinancialStatement.period_start >= period_start)
        .filter(FinancialStatement.period_start < period_start + timedelta(days=1))
        .filter(FinancialStatement.period_end >= period_end - timedelta(days=1))
        .filter(FinancialStatement.period_end <= period_end + timedelta(days=1))
        .filter(FinancialStatement.statement_type.in_(types))
        .scalar()
    )
    return count >= len(types)


def get_journal_entry_period(
    db: Session,
    *,
    company_nit: str,
) -> tuple[datetime, datetime] | None:
    """Return (min_fecha, max_fecha) from JournalEntryLine for the company, or None."""
    row = (
        db.query(
            func.min(JournalEntryLine.fecha).label("min_fecha"),
            func.max(JournalEntryLine.fecha).label("max_fecha"),
        )
        .filter(JournalEntryLine.company_nit == company_nit)
        .first()
    )
    if row is None or row.min_fecha is None:
        return None
    return (row.min_fecha, row.max_fecha)


def get_pnl(
    db: Session,
    *,
    company_nit: str,
    start_date: datetime = None,
    end_date: datetime = None,
) -> Dict[str, Any]:
    """Income Statement (Estado de Resultados) for a company and period.

    Aggregates revenue (class 4), expenses (class 5), and cost of sales (class 6)
    from JournalEntryLine rows. Returns a dict with ingresos, gastos, costo_ventas,
    utilidad_bruta, and utilidad_neta.
    """
    query = db.query(JournalEntryLine).filter(
        JournalEntryLine.company_nit == company_nit
    )
    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)

    lines = query.all()

    totals: dict[int, Decimal] = {4: Decimal("0"), 5: Decimal("0"), 6: Decimal("0")}
    detail: dict[int, dict[str, Decimal]] = {4: {}, 5: {}, 6: {}}

    for line in lines:
        if not line.cuenta_puc:
            continue
        clase = int(line.cuenta_puc[0]) if line.cuenta_puc[0].isdigit() else None
        if clase not in totals:
            continue
        # Revenue (4) is credit-nature; expenses (5,6) are debit-nature
        if clase == 4:
            amount = (line.credito or Decimal("0")) - (line.debito or Decimal("0"))
        else:
            amount = (line.debito or Decimal("0")) - (line.credito or Decimal("0"))
        totals[clase] += amount
        cuenta = line.cuenta_puc
        detail[clase][cuenta] = detail[clase].get(cuenta, Decimal("0")) + amount

    utilidad_bruta = totals[4] - totals[6]
    utilidad_neta = utilidad_bruta - totals[5]

    return {
        "ingresos": [
            {"cuenta_puc": k, "valor": float(v)} for k, v in sorted(detail[4].items())
        ],
        "gastos": [
            {"cuenta_puc": k, "valor": float(v)} for k, v in sorted(detail[5].items())
        ],
        "costo_ventas": [
            {"cuenta_puc": k, "valor": float(v)} for k, v in sorted(detail[6].items())
        ],
        "total_ingresos": float(totals[4]),
        "total_gastos": float(totals[5]),
        "total_costo_ventas": float(totals[6]),
        "utilidad_bruta": float(utilidad_bruta),
        "utilidad_neta": float(utilidad_neta),
    }


def get_journal_entry_lines(
    db: Session,
    *,
    company_nit: str,
    start_date: datetime = None,
    end_date: datetime = None,
) -> List[Dict[str, Any]]:
    """Return JournalEntryLine rows for a company as list of dicts."""
    q = db.query(JournalEntryLine).filter(JournalEntryLine.company_nit == company_nit)
    if start_date:
        q = q.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        q = q.filter(JournalEntryLine.fecha <= end_date)
    rows = q.order_by(JournalEntryLine.fecha).all()
    return [
        {
            "fecha": r.fecha.isoformat() if r.fecha else None,
            "comprobante": r.comprobante,
            "cuenta_puc": r.cuenta_puc,
            "tercero_nit": r.tercero_nit,
            "descripcion": r.descripcion,
            "debito": str(r.debito) if r.debito is not None else "0",
            "credito": str(r.credito) if r.credito is not None else "0",
        }
        for r in rows
    ]


# ─── Reportero: Analytics & Dashboard Queries ─────────────────────────────────


def get_balance_sheet_for_period(
    db: Session,
    start_date: datetime = None,
    end_date: datetime = None,
    company_nit: str | None = None,
) -> Dict:
    """Balance sheet scoped to a date range (not just cutoff, posted transactions only).

    Same logic as get_balance_sheet but filters by both start and end date.
    """
    query = (
        db.query(JournalEntryLine)
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
    )
    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)
    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)

    lines = query.all()
    totals = {
        1: Decimal("0"),
        2: Decimal("0"),
        3: Decimal("0"),
        4: Decimal("0"),
        5: Decimal("0"),
        6: Decimal("0"),
    }

    for line in lines:
        if not line.cuenta_puc:
            continue
        clase = int(line.cuenta_puc[0])
        if clase in totals:
            if clase in (1, 5, 6):
                totals[clase] += (line.debito or Decimal("0")) - (
                    line.credito or Decimal("0")
                )
            else:
                totals[clase] += (line.credito or Decimal("0")) - (
                    line.debito or Decimal("0")
                )

    net_profit = totals[4] - totals[5] - totals[6]
    # Tolerancia $1 — consistente con `get_balance_sheet` y Fix G/H.
    diff = abs(totals[1] - (totals[2] + totals[3] + net_profit))
    is_balanced = diff <= Decimal("1.00")
    return {
        "assets": float(totals[1]),
        "liabilities": float(totals[2]),
        "equity": float(totals[3]),
        "revenue": float(totals[4]),
        "expenses": float(totals[5]),
        "cost_of_sales": float(totals[6]),
        "net_profit": float(net_profit),
        "total_equity": float(totals[3] + net_profit),
        "is_balanced": is_balanced,
    }


def get_period_comparison(
    db: Session,
    p1_start: datetime,
    p1_end: datetime,
    p2_start: datetime,
    p2_end: datetime,
) -> Dict[str, Any]:
    """Return two ledger snapshots plus computed deltas per account."""
    ledger1 = get_general_ledger(db, start_date=p1_start, end_date=p1_end)
    ledger2 = get_general_ledger(db, start_date=p2_start, end_date=p2_end)

    map1 = {r["account"]: r for r in ledger1}
    map2 = {r["account"]: r for r in ledger2}
    all_accounts = sorted(set(map1.keys()) | set(map2.keys()))

    deltas: List[Dict[str, Any]] = []
    for acc in all_accounts:
        r1 = map1.get(acc, {"net_balance": 0.0, "name": ""})
        r2 = map2.get(acc, {"net_balance": 0.0, "name": ""})
        v1 = r1["net_balance"]
        v2 = r2["net_balance"]
        abs_change = v2 - v1
        pct_change = (abs_change / abs(v1) * 100) if v1 != 0 else None
        deltas.append(
            {
                "account": acc,
                "name": r1.get("name") or r2.get("name", ""),
                "period1_value": v1,
                "period2_value": v2,
                "absolute_change": abs_change,
                "percentage_change": (
                    round(pct_change, 2) if pct_change is not None else None
                ),
            }
        )

    return {
        "period1": {"start": p1_start.isoformat(), "end": p1_end.isoformat()},
        "period2": {"start": p2_start.isoformat(), "end": p2_end.isoformat()},
        "deltas": deltas,
    }


def get_top_accounts(
    db: Session,
    start_date: datetime = None,
    end_date: datetime = None,
    by: str = "debit",
    limit: int = 5,
    company_nit: str | None = None,
) -> List[Dict[str, Any]]:
    """Return top N accounts ranked by total debit or credit volume."""
    order_col = (
        func.sum(JournalEntryLine.debito)
        if by == "debit"
        else func.sum(JournalEntryLine.credito)
    )

    query = db.query(
        JournalEntryLine.cuenta_puc,
        JournalEntryLine.cuenta_nombre,
        func.sum(JournalEntryLine.debito).label("total_debit"),
        func.sum(JournalEntryLine.credito).label("total_credit"),
    ).group_by(JournalEntryLine.cuenta_puc, JournalEntryLine.cuenta_nombre)

    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)
    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)

    results = query.order_by(order_col.desc()).limit(limit).all()
    return [
        {
            "codigo": r.cuenta_puc,
            "nombre": r.cuenta_nombre,
            "total_debito": float(r.total_debit or 0),
            "total_credito": float(r.total_credit or 0),
        }
        for r in results
    ]


def get_top_terceros(
    db: Session,
    start_date: datetime = None,
    end_date: datetime = None,
    limit: int = 5,
    company_nit: str | None = None,
) -> List[Dict[str, Any]]:
    """Return top N terceros by transaction volume (count + total amount)."""
    query = (
        db.query(
            JournalEntryLine.tercero_nit,
            func.count(JournalEntryLine.id).label("num_entries"),
            func.sum(JournalEntryLine.debito).label("total_debit"),
            func.sum(JournalEntryLine.credito).label("total_credit"),
        )
        .filter(
            JournalEntryLine.tercero_nit.isnot(None),
            JournalEntryLine.tercero_nit != "",
        )
        .group_by(JournalEntryLine.tercero_nit)
    )

    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)
    if start_date:
        query = query.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        query = query.filter(JournalEntryLine.fecha <= end_date)

    results = query.order_by(func.count(JournalEntryLine.id).desc()).limit(limit).all()
    return [
        {
            "nit": r.tercero_nit,
            "num_movimientos": r.num_entries,
            "total_debito": float(r.total_debit or 0),
            "total_credito": float(r.total_credit or 0),
        }
        for r in results
    ]


def get_monthly_trend(
    db: Session,
    account_prefix: str,
    months: int = 6,
    company_nit: str | None = None,
) -> List[Dict[str, Any]]:
    """Return monthly aggregated balances for a given account prefix.

    Groups by year-month and returns totals for debit, credit, and net.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 31)

    query = (
        db.query(
            extract("year", JournalEntryLine.fecha).label("yr"),
            extract("month", JournalEntryLine.fecha).label("mo"),
            func.sum(JournalEntryLine.debito).label("total_debit"),
            func.sum(JournalEntryLine.credito).label("total_credit"),
        )
        .filter(
            JournalEntryLine.cuenta_puc.startswith(account_prefix),
            JournalEntryLine.fecha >= cutoff,
        )
        .group_by("yr", "mo")
        .order_by("yr", "mo")
    )

    if company_nit:
        query = query.filter(JournalEntryLine.company_nit == company_nit)

    results = query.all()
    return [
        {
            "month": f"{int(r.yr)}-{int(r.mo):02d}",
            "total_debit": float(r.total_debit or 0),
            "total_credit": float(r.total_credit or 0),
            "net": float((r.total_debit or 0) - (r.total_credit or 0)),
        }
        for r in results
    ]


def get_monthly_totals_by_class(
    db: Session,
    months: int = 6,
    company_nit: str | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return monthly totals for each PUC class (1-6). Used for predictions.

    Returns a dict keyed by class name with monthly data lists.
    """
    class_map = {
        "4": "ingresos",
        "5": "gastos",
        "6": "costo_ventas",
        "1": "activos",
        "2": "pasivos",
        "3": "patrimonio",
        "11": "caja",
    }
    result = {}
    for prefix, name in class_map.items():
        result[name] = get_monthly_trend(db, prefix, months, company_nit=company_nit)
    return result


def get_transaction_counts_by_status(
    db: Session, company_nit: str | None = None
) -> Dict[str, int]:
    """Return a dict mapping each TransactionStatus to its count."""
    query = db.query(TransactionPending.status, func.count(TransactionPending.id))
    if company_nit:
        query = query.filter(TransactionPending.company_nit == company_nit)
    rows = query.group_by(TransactionPending.status).all()
    return {str(status.value): count for status, count in rows}


def get_recent_activity(
    db: Session, limit: int = 10, company_nit: str | None = None
) -> List[Dict[str, Any]]:
    """Return the N most recent audit log entries, optionally scoped by NIT.

    Rows with company_nit IS NULL (e.g. process / pre-tenant entries) are
    excluded when company_nit is provided to avoid cross-tenant leakage.
    """
    query = db.query(AuditLog)
    if company_nit:
        query = query.filter(AuditLog.company_nit == company_nit)
    logs = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": log.id,
            "action": log.action,
            "entity_id": log.entity_id,
            "entity_type": log.entity_type,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


def get_municipios(db: Session) -> list[str]:
    """Return sorted distinct municipios from reteica_tarifas, excluding 'general'."""
    rows = (
        db.query(ReteicaTarifa.municipio)
        .filter(ReteicaTarifa.municipio != "general")
        .distinct()
        .order_by(ReteicaTarifa.municipio)
        .all()
    )
    return [r.municipio for r in rows]


# ─── UVT & Base Mínima ───────────────────────────────────────────


def get_uvt(db: Session, year: int) -> Decimal | None:
    """Return UVT value for given year, or None if not in DB."""
    row = db.query(UvtValue).filter(UvtValue.year == year).first()
    if row is None:
        return None
    return Decimal(str(row.value))


def get_base_minima(db: Session, concepto: str, year: int) -> Decimal | None:
    """Return UVT units for given concepto+year, or None if not in DB."""
    row = (
        db.query(TaxBaseMinima)
        .filter(TaxBaseMinima.concepto == concepto, TaxBaseMinima.year == year)
        .first()
    )
    if row is None:
        return None
    return Decimal(str(row.uvt_units))


def list_tax_constants(db: Session, year: int) -> dict:
    """Return UVT and base_minima constants for given year.

    Shape:
        {
            "uvt": {"year": int, "value": str, "decreto": str | None},
            "base_minima": [{"concepto": str, "uvt_units": str, "year": int}, ...]
        }
    """
    uvt_row = db.query(UvtValue).filter(UvtValue.year == year).first()
    bm_rows = (
        db.query(TaxBaseMinima)
        .filter(TaxBaseMinima.year == year)
        .order_by(TaxBaseMinima.concepto)
        .all()
    )
    return {
        "uvt": (
            {
                "year": uvt_row.year,
                "value": str(uvt_row.value),
                "decreto": uvt_row.decreto,
            }
            if uvt_row
            else None
        ),
        "base_minima": [
            {
                "concepto": r.concepto,
                "uvt_units": str(r.uvt_units),
                "year": r.year,
            }
            for r in bm_rows
        ],
    }


def upsert_uvt(
    db: Session,
    year: int,
    value: Decimal,
    decreto: str | None = None,
) -> UvtValue:
    """Insert or update UVT value for given year."""
    row = db.query(UvtValue).filter(UvtValue.year == year).first()
    if row is None:
        row = UvtValue(year=year, value=value, decreto=decreto)
        db.add(row)
    else:
        row.value = value
        row.decreto = decreto
    db.commit()
    db.refresh(row)
    return row


def upsert_base_minima(
    db: Session,
    concepto: str,
    uvt_units: Decimal,
    year: int,
) -> TaxBaseMinima:
    """Insert or update base mínima for given concepto+year."""
    row = (
        db.query(TaxBaseMinima)
        .filter(TaxBaseMinima.concepto == concepto, TaxBaseMinima.year == year)
        .first()
    )
    if row is None:
        row = TaxBaseMinima(concepto=concepto, uvt_units=uvt_units, year=year)
        db.add(row)
    else:
        row.uvt_units = uvt_units
    db.commit()
    db.refresh(row)
    return row
