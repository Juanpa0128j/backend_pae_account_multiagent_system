"""
Database service layer — repository pattern.
All DB operations used by agents, APIs, and the seed script go through here.
"""
# type: ignore[assignment]
# SQLAlchemy Column assignments are safe at runtime; Pylance flags them incorrectly.

import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import func, and_, cast, Integer
from sqlalchemy.orm import Session

from app.models.database import (
    IngestJob,
    IngestStatus,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
    JournalEntryLine,
    ProcessJob,
    ProcessStatus,
    AuditLog,
    CuentaPUC,
    Tercero,
)
from app.core.logger import get_logger

logger = get_logger(__name__)


def _generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    ts = int(datetime.now(timezone.utc).timestamp())
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}{ts}_{short_uuid}" if prefix else f"{ts}_{short_uuid}"


# ─── IngestJob ───────────────────────────────────────────────────

def create_ingest_job(db: Session, file_name: str, file_path: str = None) -> IngestJob:
    """Create a new ingest job for a document upload."""
    job = IngestJob(
        id=_generate_id("ing_"),
        file_name=file_name,
        file_path=file_path,
        status=IngestStatus.PENDING_PROCESSING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    create_audit_log(db, "ingest_created", job.id, "ingest", {"file_name": file_name})
    logger.info(f"Created IngestJob: {job.id}")
    return job


def update_ingest_job(
    db: Session,
    ingest_id: str,
    status: IngestStatus,
    raw_preview: Dict = None,
    extraction_errors: List[str] = None,
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
    if status in (IngestStatus.COMPLETED, IngestStatus.FAILED):
        job.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(job)
    return job


def get_ingest_job(db: Session, ingest_id: str) -> Optional[IngestJob]:
    """Get an ingest job by ID."""
    return db.query(IngestJob).filter(IngestJob.id == ingest_id).first()


# ─── TransactionPending ─────────────────────────────────────────

def create_transaction_pending(
    db: Session,
    ingest_id: str,
    fecha: datetime = None,
    nit_emisor: str = None,
    nit_receptor: str = None,
    total: Decimal = None,
    descripcion: str = None,
    items: List[Dict] = None,
    raw_data: Dict = None,
) -> TransactionPending:
    """Create a pending transaction from extracted data."""
    txn = TransactionPending(
        id=_generate_id("txn_"),
        ingest_id=ingest_id,
        fecha=fecha,
        nit_emisor=nit_emisor,
        nit_receptor=nit_receptor,
        total=total,
        descripcion=descripcion,
        items=items,
        raw_data=raw_data,
        status=TransactionStatus.PENDING,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    create_audit_log(db, "transaction_pending_created", txn.id, "transaction", {
        "ingest_id": ingest_id,
        "total": str(total) if total else None,
    })
    return txn


def get_transactions_by_ingest(db: Session, ingest_id: str) -> List[TransactionPending]:
    """Get all pending transactions for an ingest job."""
    return db.query(TransactionPending).filter(
        TransactionPending.ingest_id == ingest_id
    ).all()


def get_transactions_by_status(
    db: Session,
    status: TransactionStatus = None,
    limit: int = 50,
    offset: int = 0,
) -> List[TransactionPending]:
    """Get transactions optionally filtered by status."""
    query = db.query(TransactionPending)
    if status:
        query = query.filter(TransactionPending.status == status)
    return query.order_by(TransactionPending.created_at.desc()).offset(offset).limit(limit).all()


def get_transactions_by_nit(db: Session, nit: str, limit: int = 10) -> List[TransactionPosted]:
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
    db: Session, txn_id: str, status: TransactionStatus
) -> Optional[TransactionPending]:
    """Update a pending transaction's status."""
    txn = db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
    if txn:
        txn.status = status
        db.commit()
        db.refresh(txn)
    return txn


# ─── TransactionPosted ──────────────────────────────────────────

def create_transaction_posted(
    db: Session,
    transaction_pending_id: str,
    cuenta_puc: str,
    puc_descripcion: str = None,
    retefuente: Decimal = Decimal("0"),
    reteica: Decimal = Decimal("0"),
    iva: Decimal = Decimal("0"),
    neto_a_pagar: Decimal = Decimal("0"),
    journal_entries_json: List[Dict] = None,
    tax_references: List[str] = None,
    agent_reasoning: Dict = None,
) -> TransactionPosted:
    """Create a fully processed posted transaction."""
    posted = TransactionPosted(
        id=_generate_id("posted_"),
        transaction_pending_id=transaction_pending_id,
        cuenta_puc=cuenta_puc,
        puc_descripcion=puc_descripcion,
        retefuente=retefuente,
        reteica=reteica,
        iva=iva,
        neto_a_pagar=neto_a_pagar,
        journal_entries_json=journal_entries_json,
        tax_references=tax_references,
        agent_reasoning=agent_reasoning,
        status=TransactionStatus.POSTED,
    )
    db.add(posted)

    # Also update the pending transaction status
    pending = db.query(TransactionPending).filter(
        TransactionPending.id == transaction_pending_id
    ).first()
    if pending:
        pending.status = TransactionStatus.POSTED

    db.commit()
    db.refresh(posted)

    create_audit_log(db, "transaction_posted", posted.id, "transaction", {
        "cuenta_puc": cuenta_puc,
        "pending_id": transaction_pending_id,
    })
    return posted


# ─── JournalEntryLine ───────────────────────────────────────────

def create_journal_entry_lines(
    db: Session,
    transaction_posted_id: str,
    entries: List[Dict[str, Any]],
) -> List[JournalEntryLine]:
    """Create normalized journal entry lines for a posted transaction."""
    lines = []
    for entry in entries:
        line = JournalEntryLine(
            transaction_posted_id=transaction_posted_id,
            fecha=entry.get("fecha", datetime.now(timezone.utc)),
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
    db.commit()

    for line in lines:
        db.refresh(line)

    return lines


# ─── Accounting Books ───────────────────────────────────────────

def get_libro_diario(
    db: Session,
    fecha_inicio: datetime = None,
    fecha_fin: datetime = None,
) -> List[JournalEntryLine]:
    """Libro Diario — all journal entries in chronological order."""
    query = db.query(JournalEntryLine)
    if fecha_inicio:
        query = query.filter(JournalEntryLine.fecha >= fecha_inicio)
    if fecha_fin:
        query = query.filter(JournalEntryLine.fecha <= fecha_fin)
    return query.order_by(JournalEntryLine.fecha, JournalEntryLine.comprobante).all()


def get_libro_mayor(
    db: Session,
    fecha_inicio: datetime = None,
    fecha_fin: datetime = None,
) -> List[Dict]:
    """
    Libro Mayor — aggregated by cuenta_puc.
    Returns list of dicts with: cuenta, nombre, saldo_debito, saldo_credito, saldo_neto
    """
    query = db.query(
        JournalEntryLine.cuenta_puc,
        JournalEntryLine.cuenta_nombre,
        func.sum(JournalEntryLine.debito).label("total_debito"),
        func.sum(JournalEntryLine.credito).label("total_credito"),
    ).group_by(
        JournalEntryLine.cuenta_puc,
        JournalEntryLine.cuenta_nombre,
    )

    if fecha_inicio:
        query = query.filter(JournalEntryLine.fecha >= fecha_inicio)
    if fecha_fin:
        query = query.filter(JournalEntryLine.fecha <= fecha_fin)

    results = query.order_by(JournalEntryLine.cuenta_puc).all()

    return [
        {
            "cuenta": r.cuenta_puc,
            "nombre": r.cuenta_nombre,
            "total_debito": float(r.total_debito or 0),
            "total_credito": float(r.total_credito or 0),
            "saldo_neto": float((r.total_debito or 0) - (r.total_credito or 0)),
        }
        for r in results
    ]


def get_libro_auxiliar(
    db: Session,
    cuenta_puc: str,
    fecha_inicio: datetime = None,
    fecha_fin: datetime = None,
) -> List[JournalEntryLine]:
    """Libro Auxiliar — detail for a specific account."""
    query = db.query(JournalEntryLine).filter(JournalEntryLine.cuenta_puc == cuenta_puc)
    if fecha_inicio:
        query = query.filter(JournalEntryLine.fecha >= fecha_inicio)
    if fecha_fin:
        query = query.filter(JournalEntryLine.fecha <= fecha_fin)
    return query.order_by(JournalEntryLine.fecha).all()


def get_balance_general(db: Session, fecha_corte: datetime = None) -> Dict:
    """
    Balance General (Estado de Situación Financiera).
    Activo (clase 1) = Pasivo (clase 2) + Patrimonio (clase 3)

    Revenue (4) and Expenses (5,6) flow into retained earnings.
    """
    query = db.query(JournalEntryLine)
    if fecha_corte:
        query = query.filter(JournalEntryLine.fecha <= fecha_corte)

    # Group by first digit of cuenta_puc (clase)
    lines = query.all()

    totals = {1: Decimal("0"), 2: Decimal("0"), 3: Decimal("0"), 4: Decimal("0"), 5: Decimal("0"), 6: Decimal("0")}

    for line in lines:
        if not line.cuenta_puc:
            continue
        clase = int(line.cuenta_puc[0])
        if clase in totals:
            # Natural balance: Assets/Expenses are debit-nature, Liabilities/Equity/Revenue are credit-nature
            if clase in (1, 5, 6):  # Debit nature
                totals[clase] += (line.debito or Decimal("0")) - (line.credito or Decimal("0"))
            else:  # Credit nature (2, 3, 4)
                totals[clase] += (line.credito or Decimal("0")) - (line.debito or Decimal("0"))

    # Retained earnings = Revenue - Expenses - Cost of Sales
    utilidad_neta = totals[4] - totals[5] - totals[6]

    return {
        "activos": float(totals[1]),
        "pasivos": float(totals[2]),
        "patrimonio": float(totals[3]),
        "ingresos": float(totals[4]),
        "gastos": float(totals[5]),
        "costos": float(totals[6]),
        "utilidad_neta": float(utilidad_neta),
        "patrimonio_total": float(totals[3] + utilidad_neta),
        "cuadre": float(totals[1]) == float(totals[2] + totals[3] + utilidad_neta),
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
            (TransactionPending.nit_emisor == nit) | (TransactionPending.nit_receptor == nit)
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
    nit_emisor: str,
    total: Decimal,
    fecha: datetime,
    days_window: int = 3,
) -> List[TransactionPending]:
    """Check for potential duplicate transactions (same NIT, amount, date ±N days)."""
    fecha_inicio = fecha - timedelta(days=days_window)
    fecha_fin = fecha + timedelta(days=days_window)

    return (
        db.query(TransactionPending)
        .filter(
            TransactionPending.nit_emisor == nit_emisor,
            TransactionPending.total == total,
            TransactionPending.fecha >= fecha_inicio,
            TransactionPending.fecha <= fecha_fin,
        )
        .all()
    )


# ─── PUC ─────────────────────────────────────────────────────────

def validate_puc_exists(db: Session, codigo: str) -> Optional[CuentaPUC]:
    """Validate a PUC code exists and is active."""
    return (
        db.query(CuentaPUC)
        .filter(CuentaPUC.codigo == codigo, CuentaPUC.activa == True)
        .first()
    )


def get_all_puc(db: Session) -> List[CuentaPUC]:
    """Get all active PUC accounts."""
    return db.query(CuentaPUC).filter(CuentaPUC.activa == True).order_by(CuentaPUC.codigo).all()


def search_puc(db: Session, search_term: str, limit: int = 10) -> List[CuentaPUC]:
    """Search PUC accounts by code or name."""
    return (
        db.query(CuentaPUC)
        .filter(
            CuentaPUC.activa == True,
            (CuentaPUC.codigo.ilike(f"%{search_term}%"))
            | (CuentaPUC.nombre.ilike(f"%{search_term}%"))
        )
        .limit(limit)
        .all()
    )


# ─── ProcessJob ──────────────────────────────────────────────────

def create_process_job(db: Session, ingest_id: str) -> ProcessJob:
    """Create a new processing job."""
    job = ProcessJob(
        id=_generate_id("proc_"),
        ingest_id=ingest_id,
        status=ProcessStatus.QUEUED,
        agent_log=[],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    create_audit_log(db, "process_created", job.id, "process", {"ingest_id": ingest_id})
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

    if status:
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
        if job.agent_log is None:
            job.agent_log = []
        job.agent_log = job.agent_log + [agent_log_entry]

    db.commit()
    db.refresh(job)
    return job


def get_process_job(db: Session, process_id: str) -> Optional[ProcessJob]:
    """Get a process job by ID."""
    return db.query(ProcessJob).filter(ProcessJob.id == process_id).first()


# ─── AuditLog ────────────────────────────────────────────────────

def create_audit_log(
    db: Session,
    action: str,
    entity_id: str = None,
    entity_type: str = None,
    details: Dict = None,
) -> AuditLog:
    """Create an immutable audit log entry."""
    log = AuditLog(
        action=action,
        entity_id=entity_id,
        entity_type=entity_type,
        details=details,
    )
    db.add(log)
    db.commit()
    return log


# ─── Terceros ────────────────────────────────────────────────────

def get_or_create_tercero(
    db: Session,
    nit: str,
    razon_social: str = "Desconocido",
    tipo: str = "proveedor",
) -> Tercero:
    """Get existing tercero by NIT or create a new one."""
    tercero = db.query(Tercero).filter(Tercero.nit == nit).first()
    if not tercero:
        from app.models.database import TerceroTipo
        tercero = Tercero(
            nit=nit,
            razon_social=razon_social,
            tipo=TerceroTipo(tipo),
        )
        db.add(tercero)
        db.commit()
        db.refresh(tercero)
    return tercero
