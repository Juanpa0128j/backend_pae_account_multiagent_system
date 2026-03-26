"""
SQLAlchemy ORM models for the PAE accounting system.

Tables:
- CompanySettings: Per-tenant tax configuration (rates, régimen, city)
- Tercero: Business partners (proveedores/clientes)
- CuentaPUC: Chart of accounts (Plan Único de Cuentas colombiano)
- IngestJob: Document upload tracking
- TransactionPending: Raw extracted transactions (PENDING state)
- TransactionPosted: Fully processed transactions (POSTED state)
- JournalEntryLine: Normalized journal entries (Libro Diario source)
- ProcessJob: Async processing job tracking
- AuditLog: Immutable compliance audit trail
"""

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Column,
    String,
    Numeric,
    DateTime,
    Enum,
    Text,
    Integer,
    Boolean,
    ForeignKey,
    PrimaryKeyConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.types import JSON
JSONB = JSON().with_variant(PG_JSONB(), "postgresql")
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


# ─── Enums ───────────────────────────────────────────────────────

class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    POSTED = "posted"
    REJECTED = "rejected"
    ERROR = "error"


class ProcessStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestStatus(str, enum.Enum):
    PENDING_PROCESSING = "pending_processing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TerceroTipo(str, enum.Enum):
    PROVEEDOR = "proveedor"
    CLIENTE = "cliente"
    AMBOS = "ambos"


class NaturalezaCuenta(str, enum.Enum):
    DEBITO = "debito"
    CREDITO = "credito"


# ─── Models ──────────────────────────────────────────────────────

class CompanySettings(Base):
    """
    Per-tenant tax configuration.

    One row per company NIT. Rates are used by the tributario agent to
    calculate Retefuente, ReteICA, and IVA. Falls back to national defaults
    when no row exists for a given NIT.
    """
    __tablename__ = "company_settings"

    nit             = Column(String(20), primary_key=True, comment="Empresa NIT (tenant identifier)")
    nombre          = Column(String(255), nullable=True)
    ciudad          = Column(String(100), nullable=True)
    codigo_ciiu     = Column(String(10), nullable=True, comment="CIIU economic activity code")
    iva_responsable = Column(Boolean, default=True, nullable=False,
                             comment="True=régimen común (IVA applies), False=régimen simplificado")

    # Tax rates stored as decimal fractions (e.g. 0.110000 = 11%)
    tasa_retefuente_servicios     = Column(Numeric(8, 6), nullable=False, default=0.110000)
    tasa_retefuente_bienes        = Column(Numeric(8, 6), nullable=False, default=0.030000)
    tasa_retefuente_arrendamiento = Column(Numeric(8, 6), nullable=False, default=0.100000)
    tasa_reteica                  = Column(Numeric(8, 6), nullable=False, default=0.006900,
                                           comment="Municipal ICA retention rate")
    tasa_iva_general              = Column(Numeric(8, 6), nullable=False, default=0.190000)
    tasa_ica                      = Column(Numeric(10, 8), nullable=False, default=Decimal("0.00690000"),
                                           comment="Tarifa ICA sobre ingresos brutos (Ley 14/1983). Varía por municipio/CIIU.")
    tasa_renta                    = Column(Numeric(8, 6), nullable=False, default=Decimal("0.350000"),
                                           comment="Tarifa impuesto de renta societario — Art. 240 ET, 35% (Ley 2277/2022).")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<CompanySettings(nit={self.nit}, ciudad={self.ciudad})>"


class ReteicaTarifa(Base):
    """
    Municipal ReteICA (Retención ICA) rate lookup table.

    Stores the authoritative rate for each (municipio, ciiu_seccion) combination.
    Used by the /setup endpoint to determine the correct ReteICA rate without
    relying on LLM inference.

    Lookup priority:
      1. municipio + ciiu_seccion (e.g. 'bogota' + 'J')
      2. municipio + 'general'    (city-wide default)
      3. 'general' + 'general'    (national fallback)
    """
    __tablename__ = "reteica_tarifas"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    municipio    = Column(String(100), nullable=False, index=True,
                          comment="Lowercase normalized city name, e.g. 'bogota', 'cali'")
    ciiu_seccion = Column(String(10), nullable=False,
                          comment="CIIU section letter (A-U) or 'general' for city default")
    tasa         = Column(Numeric(10, 8), nullable=False,
                          comment="Rate as decimal fraction, e.g. 0.00966 for 0.966%")
    fuente       = Column(String(255), nullable=True,
                          comment="Legal source, e.g. 'Acuerdo 065 Bogotá 2016'")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<ReteicaTarifa(municipio={self.municipio}, ciiu={self.ciiu_seccion}, tasa={self.tasa})>"


class Tercero(Base):
    """Business partner: proveedor, cliente, or both."""
    __tablename__ = "terceros"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nit = Column(String(20), unique=True, nullable=False, index=True)
    razon_social = Column(String(255), nullable=False)
    tipo = Column(Enum(TerceroTipo), default=TerceroTipo.PROVEEDOR)
    actividad_economica = Column(String(10), nullable=True)
    direccion = Column(String(255), nullable=True)
    telefono = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Tercero(nit={self.nit}, razon_social={self.razon_social})>"


class CuentaPUC(Base):
    """Plan Único de Cuentas colombiano — chart of accounts."""
    __tablename__ = "cuentas_puc"

    id = Column(Integer, primary_key=True, autoincrement=True)
    codigo = Column(String(10), unique=True, nullable=False, index=True)
    nombre = Column(String(255), nullable=False)
    clase = Column(Integer, nullable=False, comment="1=Activo,2=Pasivo,3=Patrimonio,4=Ingreso,5=Gasto,6=Costo")
    grupo = Column(String(4), nullable=True)
    cuenta = Column(String(6), nullable=True)
    subcuenta = Column(String(8), nullable=True)
    naturaleza = Column(Enum(NaturalezaCuenta), nullable=False)
    descripcion = Column(Text, nullable=True)
    activa = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<CuentaPUC(codigo={self.codigo}, nombre={self.nombre})>"


class IngestJob(Base):
    """Tracks each document upload and its extraction status."""
    __tablename__ = "ingest_jobs"

    id = Column(String(50), primary_key=True, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=True)
    status = Column(
        Enum(IngestStatus),
        default=IngestStatus.PENDING_PROCESSING,
        nullable=False,
    )
    document_type = Column(String(50), nullable=True, comment="DocumentType enum value")
    pathway = Column(String(30), nullable=True, comment="build_from_scratch | work_with_existing")

    raw_preview = Column(JSONB, nullable=True, comment="Quick preview of extracted data")
    extraction_errors = Column(JSONB, nullable=True, comment="List of error messages")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    transactions_pending = relationship("TransactionPending", back_populates="ingest_job")
    process_jobs = relationship("ProcessJob", back_populates="ingest_job")

    def __repr__(self):
        return f"<IngestJob(id={self.id}, status={self.status})>"


class TransactionPending(Base):
    """Raw transactions extracted from ingested documents."""
    __tablename__ = "transactions_pending"

    id = Column(String(50), primary_key=True, index=True)
    ingest_id = Column(String(50), ForeignKey("ingest_jobs.id"), nullable=False, index=True)

    # Core transaction data
    fecha = Column(DateTime(timezone=True), nullable=True)
    nit_emisor = Column(String(20), nullable=True, index=True)
    nit_receptor = Column(String(20), nullable=True, index=True)
    total = Column(Numeric(15, 2), nullable=True)
    descripcion = Column(Text, nullable=True)

    # Raw extracted data
    items = Column(JSONB, nullable=True, comment="Line items from document")
    raw_data = Column(JSONB, nullable=True, comment="Full Gemini extraction result")

    status = Column(
        Enum(TransactionStatus),
        default=TransactionStatus.PENDING,
        nullable=False,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    ingest_job = relationship("IngestJob", back_populates="transactions_pending")
    transaction_posted = relationship("TransactionPosted", back_populates="transaction_pending", uselist=False)

    def __repr__(self):
        return f"<TransactionPending(id={self.id}, total={self.total}, status={self.status})>"


class TransactionPosted(Base):
    """Fully processed transactions with PUC classification and taxes."""
    __tablename__ = "transactions_posted"

    id = Column(String(50), primary_key=True, index=True)
    transaction_pending_id = Column(
        String(50),
        ForeignKey("transactions_pending.id"),
        nullable=False,
        index=True,
    )

    # PUC classification
    cuenta_puc = Column(String(10), nullable=False, index=True)
    puc_descripcion = Column(String(255), nullable=True)

    # Tax calculations (Numeric for exact accounting)
    retefuente = Column(Numeric(15, 2), default=0)
    reteica = Column(Numeric(15, 2), default=0)
    iva = Column(Numeric(15, 2), default=0)
    ica = Column(Numeric(15, 2), default=0)
    provision_renta = Column(Numeric(15, 2), default=0)
    neto_a_pagar = Column(Numeric(15, 2), default=0)

    # Journal entries as JSONB (denormalized for quick reads)
    journal_entries_json = Column(JSONB, nullable=True)

    # Agent outputs
    tax_references = Column(JSONB, nullable=True, comment="Legal references: Art. 383 ET, etc.")
    agent_reasoning = Column(JSONB, nullable=True, comment="Agent decision log per step")

    status = Column(
        Enum(TransactionStatus),
        default=TransactionStatus.POSTED,
        nullable=False,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    transaction_pending = relationship("TransactionPending", back_populates="transaction_posted")
    journal_lines = relationship("JournalEntryLine", back_populates="transaction_posted")

    def __repr__(self):
        return f"<TransactionPosted(id={self.id}, puc={self.cuenta_puc})>"


class JournalEntryLine(Base):
    """
    Normalized journal entry line — source of truth for accounting books.

    Libro Diario = SELECT * FROM journal_entry_lines ORDER BY fecha, comprobante
    Libro Mayor  = GROUP BY cuenta_puc, SUM(debito), SUM(credito)
    Auxiliar     = WHERE cuenta_puc = X ORDER BY fecha
    """
    __tablename__ = "journal_entry_lines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_posted_id = Column(
        String(50),
        ForeignKey("transactions_posted.id"),
        nullable=False,
        index=True,
    )

    fecha = Column(DateTime(timezone=True), nullable=False)
    comprobante = Column(String(20), nullable=True, comment="Voucher/receipt number")
    cuenta_puc = Column(String(10), nullable=False, index=True)
    cuenta_nombre = Column(String(255), nullable=True)
    tercero_nit = Column(String(20), nullable=True, index=True)
    descripcion = Column(Text, nullable=True)

    debito = Column(Numeric(15, 2), default=0, nullable=False)
    credito = Column(Numeric(15, 2), default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    transaction_posted = relationship("TransactionPosted", back_populates="journal_lines")

    def __repr__(self):
        return f"<JournalEntryLine(cuenta={self.cuenta_puc}, D={self.debito}, C={self.credito})>"


class ProcessJob(Base):
    """Tracks async processing jobs through the agent pipeline."""
    __tablename__ = "process_jobs"

    id = Column(String(50), primary_key=True, index=True)
    ingest_id = Column(String(50), ForeignKey("ingest_jobs.id"), nullable=False, index=True)

    status = Column(
        Enum(ProcessStatus),
        default=ProcessStatus.QUEUED,
        nullable=False,
    )
    current_stage = Column(String(50), nullable=True)
    current_agent = Column(String(50), nullable=True)
    progress = Column(Integer, default=0, comment="0-100 percent")

    error_message = Column(Text, nullable=True)
    agent_log = Column(JSONB, nullable=True, comment="Timeline of agent steps")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    ingest_job = relationship("IngestJob", back_populates="process_jobs")

    def __repr__(self):
        return f"<ProcessJob(id={self.id}, status={self.status})>"


class AuditLog(Base):
    """Immutable append-only audit trail for compliance."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(100), nullable=False, comment="e.g. transaction_created, agent_ran")
    entity_id = Column(String(50), nullable=True, index=True)
    entity_type = Column(String(50), nullable=True, comment="e.g. transaction, job, ingest")
    details = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<AuditLog(action={self.action}, entity={self.entity_type}:{self.entity_id})>"


class FinancialStatement(Base):
    """
    Stored financial statements received via Vía B (work_with_existing).

    These are pre-existing balance sheets, income statements, or auxiliary
    ledgers uploaded by the user and stored directly for reporting.
    """
    __tablename__ = "financial_statements"

    id = Column(String(50), primary_key=True, index=True)
    ingest_id = Column(String(50), ForeignKey("ingest_jobs.id"), nullable=False, index=True)
    statement_type = Column(String(50), nullable=False, comment="balance_general | estado_resultados | libro_auxiliar")
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    entity_nit = Column(String(20), nullable=True)
    data = Column(JSONB, nullable=False, comment="Full parsed financial statement data")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    ingest_job = relationship("IngestJob", backref="financial_statements")

    def __repr__(self):
        return f"<FinancialStatement(id={self.id}, type={self.statement_type})>"


class VectorDocument(Base):
    """
    Stored text documents with embeddings for RAG retrieval.

    Corresponds to the vector_documents table created in migration c3f8a2d91b5e.
    Primary key is composite: (collection_name, id).

    Note: the embedding column (vector(1024)) is intentionally omitted from
    this ORM model because SQLAlchemy does not natively understand the pgvector
    type. All embedding operations use raw SQL via sqlalchemy.text() in
    vectordb.py and rag_service.py.

    Collections used:
      - normativa_colombia_v1  : shared PUC + Estatuto Tributario (read-only)
      - empresa_{nit}_docs     : per-company documents (read/write)
    """
    __tablename__ = "vector_documents"

    id              = Column(String, nullable=False)
    collection_name = Column(String(255), nullable=False)
    content         = Column(Text, nullable=False)
    metadata_       = Column("metadata", JSONB, default=dict)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("collection_name", "id"),
    )

    def __repr__(self):
        return f"<VectorDocument(collection={self.collection_name}, id={self.id})>"
