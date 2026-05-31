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
- ChatSession: Persistent chat conversation sessions
- ChatMessage: Individual messages within a chat session
- TaxDeclarationDraft: Pre-filled DIAN declaration drafts (F300, F350, F110, ICA)
- PerdidaFiscalAcumulada: Multi-year fiscal loss carry-forward history (Art. 147 ET)
"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    String,
    Numeric,
    DateTime,
    Date,
    Enum,
    Text,
    Integer,
    Boolean,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.types import JSON

JSONB = JSON().with_variant(PG_JSONB(), "postgresql")
from sqlalchemy.orm import relationship, mapped_column, Mapped  # noqa: E402
from sqlalchemy.sql import func  # noqa: E402

from app.core.database import Base  # noqa: E402

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
    PENDING_AUDIT_REVIEW = "pending_audit_review"


class IngestStatus(str, enum.Enum):
    PENDING_PROCESSING = "pending_processing"
    PENDING_REVIEW = "pending_review"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "CANCELLED"


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

    nit = Column(
        String(20), primary_key=True, comment="Empresa NIT (tenant identifier)"
    )
    nombre = Column(String(255), nullable=True)
    ciudad = Column(String(100), nullable=True)
    codigo_ciiu = Column(
        String(10), nullable=True, comment="CIIU economic activity code"
    )
    iva_responsable = Column(
        Boolean,
        default=True,
        nullable=False,
        comment="True=régimen común (IVA applies), False=régimen simplificado",
    )
    es_declarante = Column(
        Boolean,
        default=True,
        nullable=False,
        comment="True=declarante de renta (lower retefuente rates), False=no declarante",
    )

    # Tax rates stored as decimal fractions (e.g. 0.110000 = 11%)
    tasa_retefuente_servicios = Column(
        Numeric(8, 6), nullable=False, default=0.040000
    )  # 4% declarantes (Art. 401 ET, 2026)
    tasa_retefuente_bienes = Column(
        Numeric(8, 6), nullable=False, default=0.025000
    )  # 2.5% compras declarantes
    tasa_retefuente_arrendamiento = Column(
        Numeric(8, 6),
        nullable=False,
        default=0.035000,  # 3.5% inmuebles declarantes
    )
    tasa_reteica = Column(
        Numeric(8, 6),
        nullable=False,
        default=0.006900,
        comment="Municipal ICA retention rate",
    )
    tasa_iva_general = Column(Numeric(8, 6), nullable=False, default=0.190000)
    tasa_ica = Column(
        Numeric(10, 8),
        nullable=False,
        default=Decimal("0.00690000"),
        comment="Tarifa ICA sobre ingresos brutos (Ley 14/1983). Varía por municipio/CIIU.",
    )
    tasa_renta = Column(
        Numeric(8, 6),
        nullable=False,
        default=Decimal("0.350000"),
        comment="Tarifa impuesto de renta societario — Art. 240 ET, 35% (Ley 2277/2022).",
    )

    locked_pathway = Column(
        String(30),
        nullable=True,
        comment="'build_from_scratch' (Vía A) or 'work_with_existing' (Vía B) — set on first upload, immutable after",
    )
    cuenta_ica_propio = Column(
        String(10),
        nullable=True,
        default="2368",
        comment="PUC account for ICA liability (ReteICA por pagar). Default 2368; override if company uses a different account.",
    )

    # ── Tax regime columns (added migration u1v2w3x4y5z6) ──────────────────
    regimen_tributario = Column(
        String(32),
        nullable=False,
        default="ordinario",
        server_default="ordinario",
        comment="Tax regime: ordinario | esal | zona_franca | rst",
    )
    actividad_economica = Column(
        String(32),
        nullable=False,
        default="general",
        server_default="general",
        comment="Economic activity type: general | financiero | hidroelectrico | otro",
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<CompanySettings(nit={self.nit}, ciudad={self.ciudad})>"


class TarifaRenta(Base):
    """
    Regulatory income-tax rate table for Colombian Renta PJ regimes.

    One row per (regimen, actividad, year_from) combination. Supports
    permanent surcharges (sobretasa) and temporary emergency surcharges.

    Lookup precedence (see db_service.get_tarifa_renta):
      1. Exact (regimen, actividad, year_from <= target_year <= year_to or NULL)
      2. Fallback (regimen, actividad=NULL, year matches)
      3. company_settings.tasa_renta fallback (0.35 default)

    A contador updates rows directly; no redeploy required for DIAN reform.
    """

    __tablename__ = "tarifas_renta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    regimen = Column(
        String(32),
        nullable=False,
        comment="ordinario | esal | zona_franca | rst",
    )
    actividad = Column(
        String(32),
        nullable=True,
        comment="general | financiero | hidroelectrico | otro — NULL means any actividad",
    )
    tarifa_base = Column(
        Numeric(5, 4),
        nullable=False,
        comment="Base rate as decimal fraction, e.g. 0.3500",
    )
    sobretasa = Column(
        Numeric(5, 4),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        comment="Surcharge decimal fraction, e.g. 0.0500 for financial sector",
    )
    year_from = Column(
        Integer,
        nullable=False,
        comment="First tax year this row applies",
    )
    year_to = Column(
        Integer,
        nullable=True,
        comment="Last tax year (inclusive); NULL = open-ended / currently valid",
    )
    base_legal = Column(
        String(128),
        nullable=True,
        comment="Legal authority, e.g. 'Art. 240 ET (Ley 2277/2022)'",
    )
    notas = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "regimen", "actividad", "year_from", name="uq_tarifas_renta_key"
        ),
        Index("idx_tarifas_renta_year", "year_from", "year_to"),
    )

    def __repr__(self):
        return (
            f"<TarifaRenta(regimen={self.regimen}, actividad={self.actividad}, "
            f"year_from={self.year_from}, tarifa_base={self.tarifa_base}, sobretasa={self.sobretasa})>"
        )


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

    id = Column(Integer, primary_key=True, autoincrement=True)
    municipio = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Lowercase normalized city name, e.g. 'bogota', 'cali'",
    )
    ciiu_seccion = Column(
        String(10),
        nullable=False,
        comment="CIIU section letter (A-U) or 'general' for city default",
    )
    tasa = Column(
        Numeric(10, 8),
        nullable=False,
        comment="Rate as decimal fraction, e.g. 0.00966 for 0.966%",
    )
    fuente = Column(
        String(255),
        nullable=True,
        comment="Legal source, e.g. 'Acuerdo 065 Bogotá 2016'",
    )
    base_minima_uvt = Column(
        Numeric(8, 2),
        nullable=True,
        server_default="4",
        comment=(
            "Municipal ReteICA base mínima in UVT units. "
            "Bogotá=4, Medellín=15, Cali=3. Default 4 UVT (Bogotá reference). "
            "Decreto 572 does NOT apply to ReteICA — each municipio sets its own base."
        ),
    )

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
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<Tercero(nit={self.nit}, razon_social={self.razon_social})>"


class CuentaPUC(Base):
    """Plan Único de Cuentas colombiano — chart of accounts."""

    __tablename__ = "cuentas_puc"

    id = Column(Integer, primary_key=True, autoincrement=True)
    codigo = Column(String(10), unique=True, nullable=False, index=True)
    nombre = Column(String(255), nullable=False)
    clase = Column(
        Integer,
        nullable=False,
        comment="1=Activo,2=Pasivo,3=Patrimonio,4=Ingreso,5=Gasto,6=Costo",
    )
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
    file_names = Column(JSONB, nullable=True, comment="List of all uploaded file names")
    multi_file_mode = Column(
        String(20),
        nullable=True,
        default="pages",
        comment="'pages' = concatenate as one doc | 'documents' = process each independently",
    )
    current_file_index = Column(
        Integer,
        nullable=True,
        comment="Index of file currently being parsed (0-based), for frontend progress",
    )
    status = Column(
        Enum(IngestStatus),
        default=IngestStatus.PENDING_PROCESSING,
        nullable=False,
    )
    document_type = Column(String(50), nullable=True, comment="DocumentType enum value")
    pathway = Column(
        String(30), nullable=True, comment="build_from_scratch | work_with_existing"
    )
    classification_confirmed = Column(Boolean, default=False, nullable=False)
    classification_confidence = Column(
        Numeric(4, 3),
        nullable=True,
        comment="Classifier confidence 0-1 when available",
    )
    company_nit = Column(
        String(20),
        nullable=True,
        index=True,
        comment="Tenant NIT supplied by the caller at upload time",
    )
    parser_mode = Column(
        String(20),
        nullable=False,
        default="fast",
        comment="LlamaParse extraction mode: fast|standard|premium|gpt4o",
    )

    raw_preview = Column(
        JSONB, nullable=True, comment="Quick preview of extracted data"
    )
    extraction_errors = Column(JSONB, nullable=True, comment="List of error messages")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    transactions_pending = relationship(
        "TransactionPending", back_populates="ingest_job"
    )
    process_jobs = relationship("ProcessJob", back_populates="ingest_job")

    def __repr__(self):
        return f"<IngestJob(id={self.id}, status={self.status})>"


class TransactionPending(Base):
    """Raw transactions extracted from ingested documents."""

    __tablename__ = "transactions_pending"

    id = Column(String(50), primary_key=True, index=True)
    ingest_id = Column(
        String(50), ForeignKey("ingest_jobs.id"), nullable=False, index=True
    )

    # Core transaction data
    fecha = Column(DateTime(timezone=True), nullable=True)
    company_nit = Column(
        String(20), nullable=True, index=True, comment="Owning company NIT (tenant)"
    )
    nit_emisor = Column(String(20), nullable=True, index=True)
    nit_receptor = Column(String(20), nullable=True, index=True)
    total = Column(Numeric(15, 2), nullable=True)
    descripcion = Column(Text, nullable=True)

    # Raw extracted data
    items = Column(JSONB, nullable=True, comment="Line items from document")
    raw_data = Column(JSONB, nullable=True, comment="Full Gemini extraction result")
    source_file = Column(
        String(500),
        nullable=True,
        comment="Filename of the source document (documents multi-file mode only)",
    )

    status = Column(
        Enum(TransactionStatus),
        default=TransactionStatus.PENDING,
        nullable=False,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    ingest_job = relationship("IngestJob", back_populates="transactions_pending")
    transaction_posted = relationship(
        "TransactionPosted", back_populates="transaction_pending", uselist=False
    )

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
    company_nit = Column(
        String(20), nullable=True, index=True, comment="Owning company NIT (tenant)"
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

    # IVA classification for Art. 490 ET prorrateo. See
    # app/services/tax_constants.py::TIPOS_IVA_VALIDOS for the vocabulary.
    # NULL means "no clasificado" — F300 builder treats it as gravado for
    # safety (prorateo fallback) but emits a warning.
    tipo_iva = Column(String(20), nullable=True, index=True)

    # F350 retención discrimination (Res. DIAN 000031/2024). Logical FK to
    # tax_concepts.code. NULL → "sin clasificar" (warning in F350 builder).
    concepto_retencion = Column(String(16), nullable=True, index=True)
    # PJ = persona jurídica, PN = persona natural. NULL allowed but defaults
    # to PJ for safety in F350 builder.
    tipo_persona_emisor = Column(String(2), nullable=True, index=True)

    # Journal entries as JSONB (denormalized for quick reads)
    journal_entries_json = Column(JSONB, nullable=True)

    # Agent outputs
    tax_references = Column(
        JSONB, nullable=True, comment="Legal references: Art. 383 ET, etc."
    )
    agent_reasoning = Column(
        JSONB, nullable=True, comment="Agent decision log per step"
    )

    status = Column(
        Enum(TransactionStatus),
        default=TransactionStatus.POSTED,
        nullable=False,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    transaction_pending = relationship(
        "TransactionPending", back_populates="transaction_posted"
    )
    journal_lines = relationship(
        "JournalEntryLine", back_populates="transaction_posted"
    )

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
    company_nit = Column(
        String(20), nullable=True, index=True, comment="Owning company NIT (tenant)"
    )
    comprobante = Column(String(20), nullable=True, comment="Voucher/receipt number")
    cuenta_puc = Column(String(10), nullable=False, index=True)
    cuenta_nombre = Column(String(255), nullable=True)
    tercero_nit = Column(String(20), nullable=True, index=True)
    descripcion = Column(Text, nullable=True)

    debito = Column(Numeric(15, 2), default=0, nullable=False)
    credito = Column(Numeric(15, 2), default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    transaction_posted = relationship(
        "TransactionPosted", back_populates="journal_lines"
    )

    def __repr__(self):
        return f"<JournalEntryLine(cuenta={self.cuenta_puc}, D={self.debito}, C={self.credito})>"


class ProcessJob(Base):
    """Tracks async processing jobs through the agent pipeline."""

    __tablename__ = "process_jobs"

    id = Column(String(50), primary_key=True, index=True)
    ingest_id = Column(
        String(50), ForeignKey("ingest_jobs.id"), nullable=False, index=True
    )

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
    action = Column(
        String(100), nullable=False, comment="e.g. transaction_created, agent_ran"
    )
    entity_id = Column(String(50), nullable=True, index=True)
    entity_type = Column(
        String(50), nullable=True, comment="e.g. transaction, job, ingest"
    )
    company_nit = Column(
        String(20), nullable=True, index=True, comment="Owning company NIT (tenant)"
    )
    details = Column(JSONB, nullable=True)
    created_by = Column(
        Text,
        nullable=True,
        comment="UUID of the authenticated user who triggered the action",
    )

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
    ingest_id = Column(
        String(50), ForeignKey("ingest_jobs.id"), nullable=False, index=True
    )
    statement_type = Column(
        String(50),
        nullable=False,
        comment="balance_general | estado_resultados | libro_auxiliar",
    )
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    entity_nit = Column(String(20), nullable=True)
    source_mode = Column(
        String(20),
        nullable=False,
        server_default="direct",
        comment="direct | derived | derived_from_journal",
    )
    data = Column(JSONB, nullable=False, comment="Full parsed financial statement data")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    ingest_job = relationship("IngestJob", backref="financial_statements")

    def __repr__(self):
        return f"<FinancialStatement(id={self.id}, type={self.statement_type})>"


class FinancialStatementLineage(Base):
    """
    Explicit lineage for derived financial statements.

    Each row links one derived target statement to one source input statement.
    """

    __tablename__ = "financial_statement_lineage"

    id = Column(String(50), primary_key=True, index=True)
    target_statement_id = Column(
        String(50), ForeignKey("financial_statements.id"), nullable=False, index=True
    )
    source_statement_id = Column(
        String(50), ForeignKey("financial_statements.id"), nullable=False, index=True
    )
    relation_type = Column(
        String(30), nullable=False, server_default="input", comment="input | reference"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return (
            f"<FinancialStatementLineage(target={self.target_statement_id}, "
            f"source={self.source_statement_id})>"
        )


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

    id = Column(String, nullable=False)
    collection_name = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (PrimaryKeyConstraint("collection_name", "id"),)

    def __repr__(self):
        return f"<VectorDocument(collection={self.collection_name}, id={self.id})>"


# ─── Chat ────────────────────────────────────────────────────────


class ChatSession(Base):
    """Persistent chat conversation session."""

    __tablename__ = "chat_sessions"

    id = Column(String(50), primary_key=True, index=True)
    company_nit = Column(String(20), nullable=True, index=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages = relationship(
        "ChatMessageRecord", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ChatSession(id={self.id}, nit={self.company_nit})>"


class ChatMessageRecord(Base):
    """Individual message within a chat session."""

    __tablename__ = "chat_messages"

    id = Column(String(50), primary_key=True, index=True)
    session_id = Column(
        String(50),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(10), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    data_cards = Column(
        JSONB, nullable=True
    )  # Structured financial data (assistant only)
    intent = Column(String(30), nullable=True)  # Classified intent (assistant only)
    sources = Column(JSONB, nullable=True)  # Normative references cited
    reasoning = Column(
        JSONB, nullable=True
    )  # Step-by-step trace of the agent (assistant only)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ChatSession", back_populates="messages")

    def __repr__(self):
        return f"<ChatMessageRecord(id={self.id}, role={self.role}, session={self.session_id})>"


class TaxDeclarationDraft(Base):
    """
    Pre-filled DIAN declaration draft for accountant review.

    Generated by tax_declaration_service for forms F300 (IVA), F350 (Retefuente),
    F110 (Renta PJ), and ICA Municipal. The accountant reviews fields marked
    requires_review=True before filing.

    Disclaimer: This system generates drafts only. Filing responsibility rests with
    the Contador Público (Ley 43/1990). All requires_review fields need explicit
    accountant action before submission.
    """

    __tablename__ = "tax_declaration_drafts"

    id = Column(String(50), primary_key=True, index=True)
    company_nit = Column(
        String(20),
        ForeignKey("company_settings.nit", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    form_type = Column(
        String(10), nullable=False, comment="F300 | F350 | F110 | ICA | F220"
    )
    period_start = Column(String(10), nullable=False, comment="ISO date YYYY-MM-DD")
    period_end = Column(String(10), nullable=False, comment="ISO date YYYY-MM-DD")
    year = Column(Integer, nullable=False)
    status = Column(
        String(20),
        nullable=False,
        default="draft",
        comment="draft | reviewed | filed",
    )
    fields_json = Column(
        JSONB,
        nullable=False,
        default=list,
        comment="List of {renglon, label, value, source, confidence, requires_review}",
    )
    warnings_json = Column(
        JSONB,
        nullable=False,
        default=list,
        comment="List of {field, message} for fields that need accountant review",
    )
    # Workflow audit fields (draft → reviewed → filed)
    reviewed_by = Column(String(128), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    filed_by = Column(String(128), nullable=True)
    filed_at = Column(DateTime(timezone=True), nullable=True)
    dian_acknowledgment = Column(
        String(64),
        nullable=True,
        comment="Número de radicado MUISCA — set when status=filed",
    )
    reopened_at = Column(DateTime(timezone=True), nullable=True)
    reopened_by = Column(String(128), nullable=True)
    reopen_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company = relationship("CompanySettings")

    def __repr__(self):
        return f"<TaxDeclarationDraft(id={self.id}, form={self.form_type}, nit={self.company_nit}, period={self.period_end})>"


class UvtValue(Base):
    """Yearly UVT (Unidad de Valor Tributario) published by DIAN.

    Used by tributario_agent to compute base mínima thresholds in pesos.
    Falls back to UVT_FALLBACK constant when no row exists for the year.
    """

    __tablename__ = "uvt_values"

    year = Column(Integer, primary_key=True, comment="Fiscal year, e.g. 2026")
    value = Column(
        Numeric(12, 2),
        nullable=False,
        comment="UVT value in COP pesos, e.g. 52374.00",
    )
    referencia_normativa = Column(
        String(64),
        nullable=True,
        comment=(
            "DIAN regulatory reference that published this UVT, "
            "e.g. 'Resolución 000238 de 2025'"
        ),
    )
    effective_from = Column(
        DateTime(timezone=False), nullable=True, comment="Start of validity period"
    )
    effective_to = Column(
        DateTime(timezone=False), nullable=True, comment="End of validity period"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return (
            f"<UvtValue(year={self.year}, value={self.value}, "
            f"referencia_normativa={self.referencia_normativa})>"
        )


class TaxBaseMinima(Base):
    """Base mínima (in UVT units) per concepto per year for retención thresholds.

    Conceptos: retefuente_servicios, retefuente_bienes, retefuente_arrendamiento, reteica.
    Falls back to BASE_MINIMA_RETEFUENTE_UVT / BASE_MINIMA_RETEICA_UVT constants when
    no DB row exists.
    """

    __tablename__ = "tax_base_minima"

    id = Column(Integer, primary_key=True, autoincrement=True)
    concepto = Column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "One of: retefuente_servicios, retefuente_bienes, "
            "retefuente_arrendamiento, reteica"
        ),
    )
    uvt_units = Column(
        Numeric(8, 2),
        nullable=False,
        comment="Threshold in UVT units, e.g. 4.00 for 4 UVT",
    )
    year = Column(Integer, nullable=False, index=True, comment="Fiscal year")
    effective_from = Column(
        DateTime(timezone=False), nullable=True, comment="Start of validity period"
    )
    effective_to = Column(
        DateTime(timezone=False), nullable=True, comment="End of validity period"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return (
            f"<TaxBaseMinima(concepto={self.concepto}, year={self.year}, "
            f"uvt_units={self.uvt_units})>"
        )


class TaxConcept(Base):
    """
    Catálogo de conceptos de retención en la fuente (F350 — Res. DIAN 000031/2024).

    Each row maps a retention concept to its F350 renglón, target beneficiary
    type (PJ / PN / AMB), default tarifa, base mínima UVT, and the underlying
    statute reference (Art. 392 ET, Art. 20-3 ET PES, etc.).

    A contador updates rows directly; no redeploy required when DIAN amends
    tarifas or adds new conceptos (e.g. Presencia Económica Significativa).
    """

    __tablename__ = "tax_concepts"

    code = Column(
        String(16),
        primary_key=True,
        comment="Stable identifier, e.g. 'compras_pj', 'pes_servicios_digitales'",
    )
    label = Column(String(255), nullable=False)
    renglon_350 = Column(
        String(8),
        nullable=False,
        comment="F350 renglón number (DIAN form 350)",
    )
    aplica_a = Column(
        String(4),
        nullable=False,
        comment="PJ = persona jurídica | PN = persona natural | AMB = ambos",
    )
    tarifa_default = Column(
        Numeric(6, 4),
        nullable=True,
        comment="Default retention rate as decimal fraction, e.g. 0.0250",
    )
    base_minima_uvt = Column(
        Numeric(8, 2),
        nullable=True,
        comment="Threshold in UVT below which retention does not apply",
    )
    categoria = Column(
        String(32),
        nullable=False,
        comment=(
            "compras | servicios | honorarios | arrendamiento | hidrocarburos | "
            "minerales | pes | salarios | ica | iva | otros"
        ),
    )
    art_referencia = Column(String(64), nullable=True)
    activo = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return (
            f"<TaxConcept(code={self.code}, renglon_350={self.renglon_350}, "
            f"aplica_a={self.aplica_a}, categoria={self.categoria})>"
        )


class NationalRate(Base):
    """
    Configurable Colombian statutory tax rates.

    Replaces the module-level constants in settings.py (_TASA_RETEFUENTE_*,
    tasa_renta). The /setup endpoint reads from this table so rate changes
    (e.g., legislative amendments) can be applied via settings UI without
    a code deploy.

    Seeded by Alembic migration b8c9d0e1f2a3. Codes are stable identifiers:
      'retefuente_servicios'     — Art. 392 ET
      'retefuente_bienes'        — Art. 401 ET
      'retefuente_arrendamiento' — Art. 401 ET
      'renta_general'            — Art. 240 ET, L.2277/2022
    """

    __tablename__ = "national_rates"

    code = Column(
        String(64),
        primary_key=True,
        comment="Stable identifier, e.g. 'retefuente_servicios'",
    )
    value = Column(
        Numeric(8, 6),
        nullable=False,
        comment="Rate as decimal fraction, e.g. 0.04 for 4%",
    )
    descripcion = Column(String(255), nullable=False)
    norma_referencia = Column(
        String(128),
        nullable=False,
        comment="Legal citation, e.g. 'Art. 392 ET'",
    )
    vigente_desde = Column(
        Date,
        nullable=False,
        comment="Effective date of this rate",
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<NationalRate(code={self.code!r}, value={self.value})>"


class PerdidaFiscalAcumulada(Base):
    """
    Accumulated fiscal losses per company per year (Art. 147 ET).

    Tracks multi-year loss carry-forward history (12-year limit per Art. 147 ET).
    Compensation is FIFO by year. monto_pendiente is a generated column in Postgres
    but stored as a regular column here; update via register_compensacion helper.
    """

    __tablename__ = "perdidas_fiscales_acumuladas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_nit = Column(
        String(20),
        ForeignKey("company_settings.nit", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owning company NIT (tenant)",
    )
    year = Column(Integer, nullable=False, comment="Year the loss was incurred")
    monto_perdida = Column(
        Numeric(18, 2), nullable=False, comment="Total fiscal loss for the year"
    )
    monto_compensado = Column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="Amount already compensated in subsequent years",
    )
    monto_pendiente = Column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="Pending amount = monto_perdida - monto_compensado (maintained by app)",
    )
    decreto = Column(
        String(100),
        nullable=True,
        comment="Regulatory reference, e.g. 'Art. 147 ET'",
    )
    notas = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company = relationship("CompanySettings")

    def __repr__(self):
        return (
            f"<PerdidaFiscalAcumulada(nit={self.company_nit}, year={self.year}, "
            f"pendiente={self.monto_pendiente})>"
        )


VALID_AJUSTE_SECCIONES = (
    "ESF_ACTIVO",
    "ESF_PASIVO",
    "ESF_PATRIMONIO",
    "ERI_INGRESO",
    "ERI_COSTO",
    "ERI_GASTO",
)

VALID_AJUSTE_TIPO_DIFERENCIA = (
    "permanente",
    "temporaria_imponible",
    "temporaria_deducible",
)


class AjusteFiscal(Base):
    """
    Per-company, per-year fiscal adjustments used to auto-populate F2516.

    Each row stores one concepto (e.g. 'provisiones_no_deducibles') within one
    section of the Conciliación Fiscal (Art. 772-1 ET, Res. DIAN 000049/2019).
    valor_contable is the NIIF/accounting figure; valor_fiscal is the
    tax-recognised figure. The delta drives diferencias permanentes /
    temporarias (Art. 287 ET).
    """

    __tablename__ = "ajustes_fiscales"

    id = Column(
        String(36),
        primary_key=True,
        comment="UUID generated by Postgres gen_random_uuid()",
    )
    company_nit = Column(
        String(20),
        ForeignKey("company_settings.nit", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    year = Column(Integer, nullable=False)
    seccion = Column(
        String(32),
        nullable=False,
        comment=(
            "ESF_ACTIVO | ESF_PASIVO | ESF_PATRIMONIO | "
            "ERI_INGRESO | ERI_COSTO | ERI_GASTO"
        ),
    )
    concepto = Column(String(64), nullable=False)
    valor_contable = Column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    valor_fiscal = Column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    tipo_diferencia = Column(
        String(32),
        nullable=False,
        comment="permanente | temporaria_imponible | temporaria_deducible",
    )
    descripcion = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "company_nit",
            "year",
            "seccion",
            "concepto",
            name="ajustes_fiscales_nit_year_seccion_concepto_key",
        ),
    )

    company = relationship("CompanySettings")

    def __repr__(self):
        return (
            f"<AjusteFiscal(nit={self.company_nit}, year={self.year}, "
            f"seccion={self.seccion}, concepto={self.concepto})>"
        )


class UserCompany(Base):
    """Association table linking users to companies they manage.

    user_email is denormalized so memberships can be re-associated to a fresh
    Supabase user_id when the same email signs up again (Supabase issues a new
    UUID per signup; without this column past memberships orphan).
    """

    __tablename__ = "user_company"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_nit: Mapped[str] = mapped_column(
        String, ForeignKey("company_settings.nit", ondelete="CASCADE"), primary_key=True
    )
    user_email: Mapped[str | None] = mapped_column(
        String(320), index=True, nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self):
        return f"<UserCompany(user_id={self.user_id}, company_nit={self.company_nit})>"
