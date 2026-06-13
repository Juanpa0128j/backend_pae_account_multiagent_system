"""
Database service layer — repository pattern.
All DB operations used by agents, APIs, and the seed script go through here.
"""

# type: ignore[assignment]
# SQLAlchemy Column assignments are safe at runtime; Pylance flags them incorrectly.

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, distinct, extract, func, or_
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models.database import (
    AjusteFiscal,
    AuditLog,
    CompanyPucConfig,
    CompanyRateOverride,
    CompanySettings,
    CuentaPUC,
    FinancialStatement,
    FinancialStatementLineage,
    IngestJob,
    IngestStatus,
    JournalEntryLine,
    NationalRate,
    ProcessJob,
    ProcessStatus,
    ReteicaTarifa,
    PerdidaFiscalAcumulada,
    TarifaRenta,
    TaxBaseMinima,
    TaxConcept,
    TaxDeclarationDraft,
    Tercero,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
    UserCompany,
    UvtValue,
)
from app.models.document_types import DocumentType, IngestPathway
from app.services.nit_utils import normalize_nit

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


def create_manual_ingest_job(
    db: Session,
    company_nit: str,
    created_by: str | None = None,
    commit: bool = True,
) -> IngestJob:
    """Create a synthetic ingest job for a manually-entered transaction."""
    normalized_nit = normalize_nit(company_nit)

    job = IngestJob(
        id=_generate_id("ing_"),
        file_name="manual_entry",
        file_path=None,
        file_names=None,
        multi_file_mode="pages",
        status=IngestStatus.COMPLETED,
        document_type=DocumentType.MANUAL_ENTRY.value,
        pathway=IngestPathway.BUILD_FROM_SCRATCH.value,
        classification_confirmed=True,
        company_nit=normalized_nit,
        parser_mode="fast",
    )
    db.add(job)
    create_audit_log(
        db,
        "manual_ingest_created",
        job.id,
        "ingest",
        {"company_nit": normalized_nit},
        commit=False,
        created_by=created_by,
    )
    _commit_or_flush(db, commit)
    db.refresh(job)
    logger.info("Created manual IngestJob: %s", job.id)
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


def update_transaction_pending(
    db: Session,
    txn_id: str,
    fecha: datetime | None = None,
    descripcion: str | None = None,
    total: Decimal | None = None,
    nit_emisor: str | None = None,
    nit_receptor: str | None = None,
    items: list[dict] | None = None,
    raw_data: dict | None = None,
    commit: bool = True,
) -> TransactionPending | None:
    """Partially update a pending transaction's mutable fields. Only the provided fields are updated. Returns None if the transaction is not found."""
    txn = db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
    if not txn:
        return None

    update_kwargs: dict[str, Any] = {}
    if fecha is not None:
        txn.fecha = fecha
        update_kwargs["fecha"] = fecha
    if descripcion is not None:
        txn.descripcion = descripcion
        update_kwargs["descripcion"] = descripcion
    if total is not None:
        txn.total = total
        update_kwargs["total"] = str(total)
    if nit_emisor is not None:
        txn.nit_emisor = nit_emisor
        update_kwargs["nit_emisor"] = nit_emisor
    if nit_receptor is not None:
        txn.nit_receptor = nit_receptor
        update_kwargs["nit_receptor"] = nit_receptor
    if items is not None:
        txn.items = items
        update_kwargs["items"] = items
    if raw_data is not None:
        txn.raw_data = raw_data
        update_kwargs["raw_data"] = raw_data

    create_audit_log(
        db,
        "transaction_pending_updated",
        txn_id,
        "transaction",
        {"fields_updated": list(update_kwargs.keys())},
        commit=False,
    )
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
    tipo_iva: Optional[str] = None,
    concepto_retencion: Optional[str] = None,
    tipo_persona_emisor: Optional[str] = None,
    commit: bool = True,
    created_by: str | None = None,
) -> TransactionPosted:
    """Create a fully processed posted transaction.

    ``tipo_iva`` (optional) classifies the operation under DIAN's IVA regime
    so the F300 builder can compute Art. 490 ET prorrateo. Allowed values
    live in ``app.services.tax_constants.TIPOS_IVA_VALIDOS``. ``None`` means
    "no clasificado" (the builder treats it conservatively).

    ``concepto_retencion`` (optional) maps to ``tax_concepts.code`` and lets
    the F350 builder discriminate retenciones by F350 renglón. ``None`` means
    "sin clasificar" (the builder surfaces a warning).

    ``tipo_persona_emisor`` (optional) is ``"PJ"`` or ``"PN"``. Used together
    with concepto_retencion to populate the right F350 column.
    """
    from app.services.tax_constants import is_valid_tipo_iva, is_valid_tipo_persona

    if not is_valid_tipo_iva(tipo_iva):
        raise ValueError(
            f"Invalid tipo_iva: {tipo_iva!r}. Must be one of TIPOS_IVA_VALIDOS or None."
        )
    if not is_valid_tipo_persona(tipo_persona_emisor):
        raise ValueError(
            f"Invalid tipo_persona_emisor: {tipo_persona_emisor!r}. Must be 'PJ', 'PN', or None."
        )

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
        tipo_iva=tipo_iva,
        concepto_retencion=concepto_retencion,
        tipo_persona_emisor=tipo_persona_emisor,
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


def get_revenue_by_tipo_iva(
    db: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    company_nit: Optional[str] = None,
    account_prefix: str = "4",
) -> Dict[str, float]:
    """Sum class-4 (or other prefix) credits grouped by transaction `tipo_iva`.

    Used by the F300 builder to discriminate operaciones gravadas / exentas /
    excluidas / exportaciones for Art. 490 ET prorrateo and renglones 26-30.

    Returns a dict mapping ``tipo_iva`` (or ``"sin_clasificar"`` for NULL) to
    total credits in COP for matching journal lines in the period.
    """
    rows = (
        db.query(
            TransactionPosted.tipo_iva,
            func.sum(JournalEntryLine.credito).label("total_credit"),
        )
        .join(
            JournalEntryLine,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
        .filter(JournalEntryLine.cuenta_puc.startswith(account_prefix))
    )
    if start_date:
        rows = rows.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        rows = rows.filter(JournalEntryLine.fecha <= end_date)
    if company_nit:
        rows = rows.filter(JournalEntryLine.company_nit == company_nit)

    rows = rows.group_by(TransactionPosted.tipo_iva).all()

    result: Dict[str, float] = {}
    for tipo, total in rows:
        key = tipo if tipo else "sin_clasificar"
        result[key] = float(total or 0)
    return result


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


def find_duplicate_posted(
    db: Session,
    company_nit: str,
    nit_emisor: str,
    fecha: datetime,
    total: Decimal,
) -> Optional[TransactionPosted]:
    """Return an existing TransactionPosted matching the natural key, or None.

    Natural key: (company_nit, nit_emisor, fecha::date, total).
    Used to skip duplicate postings on source-document re-upload.
    """
    day_start = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = fecha.replace(hour=23, minute=59, second=59, microsecond=999999)
    return (
        db.query(TransactionPosted)
        .join(
            TransactionPending,
            TransactionPosted.transaction_pending_id == TransactionPending.id,
        )
        .filter(
            TransactionPosted.company_nit == company_nit,
            TransactionPending.nit_emisor == nit_emisor,
            TransactionPending.fecha >= day_start,
            TransactionPending.fecha <= day_end,
            TransactionPending.total == total,
        )
        .first()
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


def deactivate_puc(
    db: Session, codigo: str, commit: bool = True
) -> Optional[CuentaPUC]:
    """Soft-delete a PUC account by setting activa=False. Returns None if not found."""
    row = db.query(CuentaPUC).filter(CuentaPUC.codigo == codigo).first()
    if not row:
        return None
    row.activa = False
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row


def get_puc_for_company(db: Session, company_nit: str) -> List[CuentaPUC]:
    """
    Get active PUC accounts for a company (OPT-OUT model).

    Returns all active PUC accounts except those explicitly deactivated
    for this company via company_puc_config with is_active=False.
    If company has no config rows, returns full active catalog.
    """
    from app.models.database import CompanyPucConfig

    # Subquery: codes deactivated for this company
    deactivated = (
        db.query(CompanyPucConfig.cuenta_codigo)
        .filter(
            CompanyPucConfig.company_nit == company_nit,
            ~CompanyPucConfig.is_active,
        )
        .subquery()
    )
    return (
        db.query(CuentaPUC)
        .filter(CuentaPUC.activa, ~CuentaPUC.codigo.in_(deactivated))
        .order_by(CuentaPUC.codigo)
        .all()
    )


def set_company_puc_config(
    db: Session,
    company_nit: str,
    cuenta_codigo: str,
    is_active: bool,
    custom_nombre: str | None = None,
    commit: bool = True,
) -> "CompanyPucConfig":
    """Upsert company PUC activation config."""
    from app.models.database import CompanyPucConfig

    row = (
        db.query(CompanyPucConfig)
        .filter_by(company_nit=company_nit, cuenta_codigo=cuenta_codigo)
        .first()
    )
    if row is None:
        row = CompanyPucConfig(company_nit=company_nit, cuenta_codigo=cuenta_codigo)
        db.add(row)
    row.is_active = is_active
    if custom_nombre is not None:
        row.custom_nombre = custom_nombre
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row


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
    frequency: str | None = None,
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
        frequency=frequency,
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


def get_reteica_base_minima_uvt(db: Session, ciudad: str, ciiu: str) -> Decimal | None:
    """Return the municipal ReteICA base mínima in UVT units for a given city+CIIU.

    Uses same 3-priority lookup as get_reteica_tarifa:
      1. municipio + ciiu_seccion (exact)
      2. municipio + 'general'   (city default)
      3. 'general' + 'general'   (national fallback)

    Returns Decimal(base_minima_uvt) or None if no row found / column null.
    Falls back to BASE_MINIMA_RETEICA_UVT constant in tributario_agent when None.
    """
    municipio = _normalize_municipio(ciudad)
    seccion = _ciiu_to_section(ciiu)

    def _extract(r: ReteicaTarifa) -> Decimal | None:
        if r is None or r.base_minima_uvt is None:
            return None
        return Decimal(str(r.base_minima_uvt))

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
            val = _extract(row)
            if val is not None:
                return val

    row = (
        db.query(ReteicaTarifa)
        .filter(
            ReteicaTarifa.municipio == municipio,
            ReteicaTarifa.ciiu_seccion == "general",
        )
        .first()
    )
    if row:
        val = _extract(row)
        if val is not None:
            return val

    row = (
        db.query(ReteicaTarifa)
        .filter(
            ReteicaTarifa.municipio == "general",
            ReteicaTarifa.ciiu_seccion == "general",
        )
        .first()
    )
    if row:
        return _extract(row)

    return None


def list_reteica_tarifas(
    db: Session,
    municipio: Optional[str] = None,
) -> list[dict]:
    """Return ReteicaTarifa rows as dicts. Optionally filter by municipio."""
    q = db.query(ReteicaTarifa)
    if municipio:
        q = q.filter(ReteicaTarifa.municipio == municipio.lower().strip())
    rows = q.order_by(ReteicaTarifa.municipio, ReteicaTarifa.ciiu_seccion).all()
    return [
        {
            "id": r.id,
            "municipio": r.municipio,
            "ciiu_seccion": r.ciiu_seccion,
            "tasa": float(r.tasa),
            "fuente": r.fuente,
            "base_minima_uvt": (
                float(r.base_minima_uvt) if r.base_minima_uvt is not None else None
            ),
        }
        for r in rows
    ]


def upsert_reteica_tarifa(
    db: Session,
    municipio: str,
    ciiu_seccion: str,
    tasa: Decimal,
    fuente: Optional[str] = None,
    base_minima_uvt: Optional[Decimal] = None,
) -> ReteicaTarifa:
    """Insert or update a ReteicaTarifa row keyed by (municipio, ciiu_seccion)."""
    municipio = municipio.lower().strip()
    row = (
        db.query(ReteicaTarifa)
        .filter(
            ReteicaTarifa.municipio == municipio,
            ReteicaTarifa.ciiu_seccion == ciiu_seccion,
        )
        .first()
    )
    if row is None:
        row = ReteicaTarifa(
            municipio=municipio,
            ciiu_seccion=ciiu_seccion,
            tasa=tasa,
            fuente=fuente,
            base_minima_uvt=base_minima_uvt,
        )
        db.add(row)
    else:
        row.tasa = tasa
        row.fuente = fuente
        row.base_minima_uvt = base_minima_uvt
    db.commit()
    db.refresh(row)
    return row


def delete_reteica_tarifa(db: Session, row_id: int) -> bool:
    """Hard-delete a ReteicaTarifa row by id. Returns True if deleted."""
    row = db.query(ReteicaTarifa).filter(ReteicaTarifa.id == row_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


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
    """Return JournalEntryLine rows for a company as list of dicts.

    Only includes lines whose parent TransactionPosted has status=POSTED so
    that REJECTED/ERROR transactions are excluded from accounting reports.
    """
    from app.models.database import TransactionPosted, TransactionStatus

    q = (
        db.query(JournalEntryLine)
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(
            JournalEntryLine.company_nit == company_nit,
            TransactionPosted.status == TransactionStatus.POSTED,
        )
    )
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
    # Exact N-month rollback (no `timedelta(days=N*31)` drift).
    now = datetime.now(timezone.utc)
    cutoff_year = now.year
    cutoff_month = now.month - months
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    cutoff = now.replace(
        year=cutoff_year,
        month=cutoff_month,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    # Join + status filter so charts only reflect POSTED transactions.
    # Without this, PENDING_REVIEW and REJECTED journal lines leak into the
    # monthly Ingresos/Gastos trend and the KPI cards that consume it.
    query = (
        db.query(
            extract("year", JournalEntryLine.fecha).label("yr"),
            extract("month", JournalEntryLine.fecha).label("mo"),
            func.sum(JournalEntryLine.debito).label("total_debit"),
            func.sum(JournalEntryLine.credito).label("total_credit"),
        )
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(
            TransactionPosted.status == TransactionStatus.POSTED,
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


def get_base_minima(
    db: Session,
    concepto: str,
    year: int,
    as_of_date: date | None = None,
) -> Decimal | None:
    """Return UVT units for given concepto+year (or as_of_date), or None.

    If as_of_date is provided, filters rows where:
        effective_from <= as_of_date AND (effective_to IS NULL OR effective_to >= as_of_date)
    This enables temporal lookup for regulatory windows (e.g. Decreto 572 suspension
    by Consejo de Estado on May 7 2026 → documents dated before/after use different bases).

    If as_of_date is None, falls back to year-based lookup (most recently inserted row
    matching the year), preserving backward compatibility.
    """
    if as_of_date is not None:
        row = (
            db.query(TaxBaseMinima)
            .filter(
                TaxBaseMinima.concepto == concepto,
                TaxBaseMinima.year == year,
                TaxBaseMinima.effective_from <= as_of_date,
                or_(
                    TaxBaseMinima.effective_to.is_(None),
                    TaxBaseMinima.effective_to >= as_of_date,
                ),
            )
            .first()
        )
    else:
        row = (
            db.query(TaxBaseMinima)
            .filter(TaxBaseMinima.concepto == concepto, TaxBaseMinima.year == year)
            .first()
        )
    if row is None:
        return None
    return Decimal(str(row.uvt_units))


def list_tax_constants(db: Session, year: int) -> dict:
    """Return UVT, base_minima, tarifas_renta and tax_concepts for given year.

    Base mínima de-duplicated to one row per concept (latest effective_from
    for that year), so the settings UI does not show overlapping temporal
    windows as duplicates.

    Shape:
        {
            "uvt": {"year": int, "value": str, "referencia_normativa": str | None},
            "base_minima": [{"concepto": str, "uvt_units": str, "year": int}, ...],
            "tarifas_renta": [dict, ...],
            "tax_concepts": [dict, ...],
        }

    base_minima returns only the currently-effective row per concepto for the
    requested ``year`` (1 row per concepto). The migration that dropped the
    ``UNIQUE(concepto, year)`` constraint allows multiple temporal windows
    (e.g. Decreto 572 ranges); this filter picks the one whose
    ``[effective_from, effective_to]`` covers the as-of date — ``today`` when
    consulting the current year, ``Dec 31 of year`` when looking at a past or
    future year (point-in-time semantics).
    """
    uvt_row = db.query(UvtValue).filter(UvtValue.year == year).first()

    today = date.today()
    asof = today if today.year == year else date(year, 12, 31)
    # Two windows can legitimately cover the same as-of date (legacy row
    # with effective_from=NULL + Decreto 572 row 2025-06-01 → NULL, both
    # vigentes for asof=2026-05-31). Order by concepto then most-recent
    # effective_from first, then dedup in Python so the docstring promise
    # of "1 row per concepto" actually holds and the UI doesn't crash on
    # duplicate React keys.
    candidate_rows = (
        db.query(TaxBaseMinima)
        .filter(TaxBaseMinima.year == year)
        .filter(
            or_(
                TaxBaseMinima.effective_from.is_(None),
                TaxBaseMinima.effective_from <= asof,
            )
        )
        .filter(
            or_(
                TaxBaseMinima.effective_to.is_(None),
                TaxBaseMinima.effective_to >= asof,
            )
        )
        .order_by(TaxBaseMinima.concepto, desc(TaxBaseMinima.effective_from))
        .all()
    )
    seen_concepts: set[str] = set()
    bm_rows = []
    for row in candidate_rows:
        if row.concepto in seen_concepts:
            continue
        seen_concepts.add(row.concepto)
        bm_rows.append(row)

    return {
        "uvt": (
            {
                "year": uvt_row.year,
                "value": str(uvt_row.value),
                "referencia_normativa": uvt_row.referencia_normativa,
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
        "tarifas_renta": list_tarifas_renta(db, year=year),
        "tax_concepts": list_tax_concepts(db, activo=None),
    }


def upsert_uvt(
    db: Session,
    year: int,
    value: Decimal,
    referencia_normativa: str | None = None,
) -> UvtValue:
    """Insert or update UVT value for given year."""
    row = db.query(UvtValue).filter(UvtValue.year == year).first()
    if row is None:
        row = UvtValue(
            year=year, value=value, referencia_normativa=referencia_normativa
        )
        db.add(row)
    else:
        row.value = value
        row.referencia_normativa = referencia_normativa
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


# ---------------------------------------------------------------------------
# Pérdidas fiscales acumuladas (Art. 147 ET — 12-year carry-forward)
# ---------------------------------------------------------------------------


def get_perdidas_disponibles(
    db: Session, company_nit: str, current_year: int
) -> list[PerdidaFiscalAcumulada]:
    """
    Return rows with monto_pendiente > 0 from years prior to current_year.
    Ordered ASC by year for FIFO compensation per Art. 147 ET.
    """
    return (
        db.query(PerdidaFiscalAcumulada)
        .filter(
            PerdidaFiscalAcumulada.company_nit == company_nit,
            PerdidaFiscalAcumulada.year < current_year,
            PerdidaFiscalAcumulada.monto_pendiente > 0,
        )
        .order_by(PerdidaFiscalAcumulada.year.asc())
        .all()
    )


def sum_perdidas_disponibles(
    db: Session, company_nit: str, current_year: int
) -> Decimal:
    """Sum of all available (pending) fiscal losses prior to current_year."""
    rows = get_perdidas_disponibles(db, company_nit, current_year)
    return sum((r.monto_pendiente for r in rows), Decimal("0"))


def upsert_perdida(
    db: Session,
    company_nit: str,
    year: int,
    monto_perdida: Decimal,
    decreto: str | None = None,
    notas: str | None = None,
) -> PerdidaFiscalAcumulada:
    """Insert or update a fiscal loss record for the given company and year."""
    row = (
        db.query(PerdidaFiscalAcumulada)
        .filter(
            PerdidaFiscalAcumulada.company_nit == company_nit,
            PerdidaFiscalAcumulada.year == year,
        )
        .first()
    )
    if row is None:
        row = PerdidaFiscalAcumulada(
            company_nit=company_nit,
            year=year,
            monto_perdida=monto_perdida,
            monto_compensado=Decimal("0"),
            monto_pendiente=monto_perdida,
            decreto=decreto,
            notas=notas,
        )
        db.add(row)
    else:
        row.monto_perdida = monto_perdida
        # Recalculate pending after updating total
        row.monto_pendiente = monto_perdida - row.monto_compensado
        if decreto is not None:
            row.decreto = decreto
        if notas is not None:
            row.notas = notas
    db.commit()
    db.refresh(row)
    return row


def register_compensacion(
    db: Session,
    company_nit: str,
    year: int,
    monto_compensado_delta: Decimal,
) -> PerdidaFiscalAcumulada:
    """
    Increment monto_compensado for the given year's loss record.
    Raises ValueError if delta would exceed monto_perdida.
    """
    row = (
        db.query(PerdidaFiscalAcumulada)
        .filter(
            PerdidaFiscalAcumulada.company_nit == company_nit,
            PerdidaFiscalAcumulada.year == year,
        )
        .first()
    )
    if row is None:
        raise ValueError(
            f"No fiscal loss record found for NIT {company_nit}, year {year}"
        )
    new_compensado = row.monto_compensado + monto_compensado_delta
    if new_compensado > row.monto_perdida:
        raise ValueError(
            f"Compensación de {monto_compensado_delta} excedería la pérdida total "
            f"de {row.monto_perdida} para año {year}. "
            f"Ya compensado: {row.monto_compensado}."
        )
    row.monto_compensado = new_compensado
    row.monto_pendiente = row.monto_perdida - new_compensado
    db.commit()
    db.refresh(row)
    return row


def sum_retenciones_anio(db: Session, company_nit: str, year: int) -> Decimal:
    """
    Sum of retenciones a favor for the given year.
    Sources: PUC 135515 + 135518 debit balances from journal_entry_lines (Jan 1 – Dec 31).
    """
    start_dt = datetime(year, 1, 1, 0, 0, 0)
    end_dt = datetime(year, 12, 31, 23, 59, 59)

    result = (
        db.query(func.sum(JournalEntryLine.debito))
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(
            TransactionPosted.status == TransactionStatus.POSTED,
            JournalEntryLine.company_nit == company_nit,
            JournalEntryLine.cuenta_puc.in_(["135515", "135518"]),
            JournalEntryLine.fecha >= start_dt,
            JournalEntryLine.fecha <= end_dt,
        )
        .scalar()
    )
    return Decimal(str(result or 0))


def get_latest_f2516_reviewed(
    db: Session, company_nit: str, year: int
) -> "TaxDeclarationDraft | None":
    """Return the latest F2516 draft with status='reviewed' for the given year, or None."""
    return (
        db.query(TaxDeclarationDraft)
        .filter(
            TaxDeclarationDraft.company_nit == company_nit,
            TaxDeclarationDraft.form_type == "F2516",
            TaxDeclarationDraft.year == year,
            TaxDeclarationDraft.status == "reviewed",
        )
        .order_by(TaxDeclarationDraft.created_at.desc())
        .first()
    )


def get_impuesto_neto_anio(db: Session, company_nit: str, year: int) -> Decimal | None:
    """Return renglón 88 (impuesto neto de renta) of the latest F110 draft for `year`.

    Used by the Art. 807 anticipo "promedio de los dos últimos años" method
    (método 2). Returns ``None`` when no F110 draft exists for that year or the
    renglón is missing/unparseable — the caller then falls back to método 1.
    """
    draft = (
        db.query(TaxDeclarationDraft)
        .filter(
            TaxDeclarationDraft.company_nit == company_nit,
            TaxDeclarationDraft.form_type == "F110",
            TaxDeclarationDraft.year == year,
        )
        .order_by(TaxDeclarationDraft.created_at.desc())
        .first()
    )
    if draft is None:
        return None
    for fld in draft.fields_json or []:
        if fld.get("renglon") == "88":
            try:
                return Decimal(str(fld.get("value")))
            except (TypeError, ValueError, ArithmeticError):
                return None
    return None


# ---------------------------------------------------------------------------
# TarifaRenta helpers — Colombian Renta PJ regulatory rate table
# ---------------------------------------------------------------------------


def get_tarifa_renta(
    db: Session, regimen: str, actividad: str, year: int
) -> dict | None:
    """Return {tarifa_base, sobretasa, tarifa_efectiva, base_legal} or None.

    Lookup precedence:
    1. Exact (regimen, actividad, year in [year_from, year_to or inf])
       When multiple rows match (e.g. emergency surcharge with higher year_from),
       return the most specific — highest year_from <= year.
    2. Fallback (regimen, actividad=NULL, year matches)
    3. None — caller should fall back to company_settings.tasa_renta
    """

    def _row_to_dict(row: TarifaRenta) -> dict:
        tarifa_base = Decimal(str(row.tarifa_base))
        sobretasa = Decimal(str(row.sobretasa))
        return {
            "tarifa_base": float(tarifa_base),
            "sobretasa": float(sobretasa),
            "tarifa_efectiva": float(tarifa_base + sobretasa),
            "base_legal": row.base_legal,
        }

    def _year_filter(q):
        return q.filter(
            TarifaRenta.year_from <= year,
            (TarifaRenta.year_to == None) | (TarifaRenta.year_to >= year),  # noqa: E711
        )

    # 1. Exact match (regimen + actividad)
    row = (
        _year_filter(
            db.query(TarifaRenta).filter(
                TarifaRenta.regimen == regimen,
                TarifaRenta.actividad == actividad,
            )
        )
        .order_by(TarifaRenta.year_from.desc())
        .first()
    )
    if row:
        return _row_to_dict(row)

    # 2. Fallback — actividad=NULL (covers any actividad for this regimen)
    row = (
        _year_filter(
            db.query(TarifaRenta).filter(
                TarifaRenta.regimen == regimen,
                TarifaRenta.actividad == None,  # noqa: E711
            )
        )
        .order_by(TarifaRenta.year_from.desc())
        .first()
    )
    if row:
        return _row_to_dict(row)

    return None


def list_tarifas_renta(db: Session, year: int | None = None) -> list[dict]:
    """List all tarifas_renta rows, optionally filtered to those applicable for a year."""
    q = db.query(TarifaRenta)
    if year is not None:
        q = q.filter(
            TarifaRenta.year_from <= year,
            (TarifaRenta.year_to == None) | (TarifaRenta.year_to >= year),  # noqa: E711
        )
    rows = q.order_by(
        TarifaRenta.regimen, TarifaRenta.actividad, TarifaRenta.year_from
    ).all()
    return [
        {
            "id": r.id,
            "regimen": r.regimen,
            "actividad": r.actividad,
            "tarifa_base": float(r.tarifa_base),
            "sobretasa": float(r.sobretasa),
            "tarifa_efectiva": float(
                Decimal(str(r.tarifa_base)) + Decimal(str(r.sobretasa))
            ),
            "year_from": r.year_from,
            "year_to": r.year_to,
            "base_legal": r.base_legal,
            "notas": r.notas,
        }
        for r in rows
    ]


def upsert_tarifa_renta(
    db: Session,
    regimen: str,
    actividad: str | None,
    tarifa_base: Decimal,
    year_from: int,
    sobretasa: Decimal = Decimal("0"),
    year_to: int | None = None,
    base_legal: str | None = None,
    notas: str | None = None,
) -> TarifaRenta:
    """Insert or update a tarifa_renta row keyed by (regimen, actividad, year_from)."""
    row = (
        db.query(TarifaRenta)
        .filter(
            TarifaRenta.regimen == regimen,
            TarifaRenta.actividad == actividad,
            TarifaRenta.year_from == year_from,
        )
        .first()
    )
    if row is None:
        row = TarifaRenta(
            regimen=regimen,
            actividad=actividad,
            tarifa_base=tarifa_base,
            sobretasa=sobretasa,
            year_from=year_from,
            year_to=year_to,
            base_legal=base_legal,
            notas=notas,
        )
        db.add(row)
    else:
        row.tarifa_base = tarifa_base
        row.sobretasa = sobretasa
        row.year_to = year_to
        if base_legal is not None:
            row.base_legal = base_legal
        if notas is not None:
            row.notas = notas
    db.commit()
    db.refresh(row)
    return row


# ─── TaxConcept helpers (F350 — Res. DIAN 000031/2024) ─────────────────────


def _tax_concept_to_dict(row: TaxConcept) -> dict:
    return {
        "code": row.code,
        "label": row.label,
        "renglon_350": row.renglon_350,
        "aplica_a": row.aplica_a,
        "tarifa_default": (
            float(row.tarifa_default) if row.tarifa_default is not None else None
        ),
        "base_minima_uvt": (
            float(row.base_minima_uvt) if row.base_minima_uvt is not None else None
        ),
        "categoria": row.categoria,
        "art_referencia": row.art_referencia,
        "activo": bool(row.activo),
    }


def list_tax_concepts(db: Session, activo: bool | None = True) -> list[dict]:
    """List tax_concepts rows. Pass ``activo=None`` to include soft-deleted."""
    q = db.query(TaxConcept)
    if activo is not None:
        q = q.filter(TaxConcept.activo == activo)
    rows = q.order_by(TaxConcept.renglon_350, TaxConcept.code).all()
    return [_tax_concept_to_dict(r) for r in rows]


def get_tax_concept(db: Session, code: str) -> TaxConcept | None:
    """Return the TaxConcept row keyed by code, or None."""
    return db.query(TaxConcept).filter(TaxConcept.code == code).first()


def upsert_tax_concept(
    db: Session,
    code: str,
    label: str,
    renglon_350: str,
    aplica_a: str,
    categoria: str,
    tarifa_default: Decimal | None = None,
    base_minima_uvt: Decimal | None = None,
    art_referencia: str | None = None,
    activo: bool = True,
) -> TaxConcept:
    """Insert or update a tax_concepts row keyed by code."""
    from app.services.tax_constants import is_valid_aplica_a

    if not is_valid_aplica_a(aplica_a):
        raise ValueError(
            f"Invalid aplica_a: {aplica_a!r}. Must be 'PJ', 'PN', or 'AMB'."
        )

    row = db.query(TaxConcept).filter(TaxConcept.code == code).first()
    if row is None:
        row = TaxConcept(
            code=code,
            label=label,
            renglon_350=renglon_350,
            aplica_a=aplica_a,
            categoria=categoria,
            tarifa_default=tarifa_default,
            base_minima_uvt=base_minima_uvt,
            art_referencia=art_referencia,
            activo=activo,
        )
        db.add(row)
    else:
        row.label = label
        row.renglon_350 = renglon_350
        row.aplica_a = aplica_a
        row.categoria = categoria
        row.tarifa_default = tarifa_default
        row.base_minima_uvt = base_minima_uvt
        row.art_referencia = art_referencia
        row.activo = activo
    db.commit()
    db.refresh(row)
    return row


def soft_delete_tax_concept(db: Session, code: str) -> TaxConcept | None:
    """Mark a tax_concepts row as inactive. Returns the row, or None if missing."""
    row = db.query(TaxConcept).filter(TaxConcept.code == code).first()
    if row is None:
        return None
    row.activo = False
    db.commit()
    db.refresh(row)
    return row


def sum_retencion_by_concepto(
    db: Session,
    concepto_code: str,
    *,
    company_nit: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Decimal:
    """Sum credits on retención liability accounts for a concepto.

    Resolution per categoría:
      * salarios → SUM(credito) WHERE cuenta_puc LIKE '2365%' OR '2367%'
        (no concepto filter — salarios live outside concepto_retencion).
      * ica      → SUM(credito) WHERE cuenta_puc LIKE '2368%'
      * default  → SUM(credito) WHERE cuenta_puc LIKE '2365%'
        AND transactions_posted.concepto_retencion = concepto_code.
    """
    concept = get_tax_concept(db, concepto_code)
    if concept is None:
        return Decimal("0")

    q = (
        db.query(func.coalesce(func.sum(JournalEntryLine.credito), 0))
        .join(
            TransactionPosted,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        .filter(TransactionPosted.status == TransactionStatus.POSTED)
    )

    if concept.categoria == "salarios":
        q = q.filter(
            or_(
                JournalEntryLine.cuenta_puc.startswith("2365"),
                JournalEntryLine.cuenta_puc.startswith("2367"),
            )
        )
    elif concept.categoria == "ica":
        q = q.filter(JournalEntryLine.cuenta_puc.startswith("2368"))
    else:
        q = q.filter(JournalEntryLine.cuenta_puc.startswith("2365"))
        q = q.filter(TransactionPosted.concepto_retencion == concepto_code)

    if company_nit:
        q = q.filter(JournalEntryLine.company_nit == company_nit)
    if start_date:
        q = q.filter(JournalEntryLine.fecha >= start_date)
    if end_date:
        q = q.filter(JournalEntryLine.fecha <= end_date)

    total = q.scalar() or 0
    return Decimal(str(total))


def count_unclassified_retenciones(
    db: Session,
    *,
    company_nit: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    """Count POSTED transactions with retefuente > 0 but no concepto_retencion."""
    q = db.query(func.count(TransactionPosted.id)).filter(
        TransactionPosted.status == TransactionStatus.POSTED,
        TransactionPosted.retefuente > 0,
        TransactionPosted.concepto_retencion.is_(None),
    )
    if company_nit:
        q = q.filter(TransactionPosted.company_nit == company_nit)
    if start_date or end_date:
        q = q.join(
            JournalEntryLine,
            JournalEntryLine.transaction_posted_id == TransactionPosted.id,
        )
        if start_date:
            q = q.filter(JournalEntryLine.fecha >= start_date)
        if end_date:
            q = q.filter(JournalEntryLine.fecha <= end_date)
    return int(q.scalar() or 0)


# ---------------------------------------------------------------------------
# AjusteFiscal helpers — F2516 auto-poblado
# ---------------------------------------------------------------------------


def list_ajustes_fiscales(
    db: Session,
    company_nit: str,
    year: int,
    seccion: Optional[str] = None,
) -> list[AjusteFiscal]:
    """Return all ajustes_fiscales rows for (nit, year), optionally filtered by seccion."""
    q = db.query(AjusteFiscal).filter(
        AjusteFiscal.company_nit == company_nit,
        AjusteFiscal.year == year,
    )
    if seccion is not None:
        q = q.filter(AjusteFiscal.seccion == seccion)
    return q.order_by(AjusteFiscal.seccion.asc(), AjusteFiscal.concepto.asc()).all()


def upsert_ajuste_fiscal(
    db: Session,
    *,
    company_nit: str,
    year: int,
    seccion: str,
    concepto: str,
    valor_contable: Decimal,
    valor_fiscal: Decimal,
    tipo_diferencia: str,
    descripcion: Optional[str] = None,
) -> AjusteFiscal:
    """Insert or update a single ajuste fiscal row keyed by (nit, year, seccion, concepto)."""
    row = (
        db.query(AjusteFiscal)
        .filter(
            AjusteFiscal.company_nit == company_nit,
            AjusteFiscal.year == year,
            AjusteFiscal.seccion == seccion,
            AjusteFiscal.concepto == concepto,
        )
        .first()
    )
    if row is None:
        row = AjusteFiscal(
            id=str(uuid.uuid4()),
            company_nit=company_nit,
            year=year,
            seccion=seccion,
            concepto=concepto,
            valor_contable=valor_contable,
            valor_fiscal=valor_fiscal,
            tipo_diferencia=tipo_diferencia,
            descripcion=descripcion,
        )
        db.add(row)
    else:
        row.valor_contable = valor_contable
        row.valor_fiscal = valor_fiscal
        row.tipo_diferencia = tipo_diferencia
        if descripcion is not None:
            row.descripcion = descripcion
    db.commit()
    db.refresh(row)
    return row


def delete_ajuste_fiscal(db: Session, ajuste_id: str) -> bool:
    """Hard delete an ajuste fiscal row. Returns True if deleted, False if not found."""
    row = db.query(AjusteFiscal).filter(AjusteFiscal.id == ajuste_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# ── NationalRate ─────────────────────────────────────────────────────────────


def list_national_rates(db: Session) -> list[dict]:
    """Return all national_rates rows as dicts, ordered by code."""
    rows = db.query(NationalRate).order_by(NationalRate.code).all()
    return [
        {
            "code": r.code,
            "value": float(r.value),
            "descripcion": r.descripcion,
            "norma_referencia": r.norma_referencia,
            "vigente_desde": r.vigente_desde.isoformat() if r.vigente_desde else None,
        }
        for r in rows
    ]


def get_national_rate(db: Session, code: str) -> NationalRate | None:
    """Return the NationalRate row for the given code, or None."""
    return db.query(NationalRate).filter(NationalRate.code == code).first()


def upsert_national_rate(
    db: Session,
    code: str,
    value: Decimal,
    descripcion: str,
    norma_referencia: str,
    vigente_desde: date,
) -> NationalRate:
    """Insert or update a NationalRate row keyed by code."""
    row = db.query(NationalRate).filter(NationalRate.code == code).first()
    if row is None:
        row = NationalRate(
            code=code,
            value=value,
            descripcion=descripcion,
            norma_referencia=norma_referencia,
            vigente_desde=vigente_desde,
        )
        db.add(row)
    else:
        row.value = value
        row.descripcion = descripcion
        row.norma_referencia = norma_referencia
        row.vigente_desde = vigente_desde
    db.commit()
    db.refresh(row)
    return row


# ── Company Rate Overrides ───────────────────────────────────────────────────


def get_effective_rates(db: Session, company_nit: str) -> list[dict]:
    """
    Return effective rates for a company: company override → national_rates fallback.

    Returns all national rates with company overrides layered on top.
    Each rate dict includes an 'overridden' flag (True if company has override).
    """
    nationals = {r["code"]: r for r in list_national_rates(db)}
    overrides = (
        db.query(CompanyRateOverride)
        .filter(CompanyRateOverride.company_nit == company_nit)
        .all()
    )
    result = {code: {**data, "overridden": False} for code, data in nationals.items()}
    for ov in overrides:
        if ov.rate_code in result:
            result[ov.rate_code].update(
                {
                    "value": float(ov.value),
                    "norma_referencia": ov.norma_referencia
                    or result[ov.rate_code]["norma_referencia"],
                    "overridden": True,
                }
            )
    return list(result.values())


def upsert_company_rate_override(
    db: Session,
    company_nit: str,
    rate_code: str,
    value: Decimal,
    norma_referencia: str | None,
    vigente_desde: date,
    commit: bool = True,
) -> CompanyRateOverride:
    """Upsert a company-specific rate override."""
    row = (
        db.query(CompanyRateOverride)
        .filter_by(company_nit=company_nit, rate_code=rate_code)
        .first()
    )
    if row is None:
        row = CompanyRateOverride(company_nit=company_nit, rate_code=rate_code)
        db.add(row)
    row.value = value
    row.norma_referencia = norma_referencia
    row.vigente_desde = vigente_desde
    _commit_or_flush(db, commit)
    db.refresh(row)
    return row
