"""
Database integration tests for the PAE accounting system.

These tests verify:
1. ORM models can be created and queried
2. db_service CRUD operations work correctly
3. Accounting book queries (Diario, Mayor, Auxiliar, Balance) return correct data
4. Double-entry (partida doble) invariants hold
5. Duplicate detection works
6. PUC seed data is correct

Requires a running PostgreSQL database at DATABASE_URL.
Tests use transactions that are rolled back after each test.
"""

import os
import pytest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import (
    Tercero,
    TerceroTipo,
    CuentaPUC,
    NaturalezaCuenta,
    IngestJob,
    IngestStatus,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
    JournalEntryLine,
    ProcessJob,
    ProcessStatus,
    AuditLog,
)
from app.models.document_types import ParserMode
from app.services import db_service

# ─── Fixtures ────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pae_user:password@localhost:5432/pae_accounting",
)


@pytest.fixture(scope="session")
def engine():
    """Create a test database engine; skip test session if DB is unreachable."""
    eng = create_engine(DATABASE_URL, echo=False, connect_args={"connect_timeout": 2})
    try:
        conn = eng.connect()
        conn.close()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available at {DATABASE_URL!r}: {exc}")
    Base.metadata.create_all(bind=eng)
    yield eng


@pytest.fixture
def db(engine):
    """
    Create a transactional test session.
    Each test runs in its own transaction that is rolled back at the end.
    """
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    # Begin a nested transaction (savepoint) so the test can commit internally
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def end_savepoint(session, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def sample_puc(db):
    """Insert a few PUC accounts for testing."""
    accounts = [
        CuentaPUC(
            codigo="519595",
            nombre="Gastos Diversos",
            clase=5,
            naturaleza=NaturalezaCuenta.DEBITO,
            activa=True,
        ),
        CuentaPUC(
            codigo="220505",
            nombre="Proveedores Nacionales",
            clase=2,
            naturaleza=NaturalezaCuenta.CREDITO,
            activa=True,
        ),
        CuentaPUC(
            codigo="240802",
            nombre="IVA Descontable",
            clase=2,
            naturaleza=NaturalezaCuenta.DEBITO,
            activa=True,
        ),
        CuentaPUC(
            codigo="240815",
            nombre="Retefuente Servicios",
            clase=2,
            naturaleza=NaturalezaCuenta.CREDITO,
            activa=True,
        ),
        CuentaPUC(
            codigo="110505",
            nombre="Caja General",
            clase=1,
            naturaleza=NaturalezaCuenta.DEBITO,
            activa=True,
        ),
    ]
    for acc in accounts:
        existing = db.query(CuentaPUC).filter(CuentaPUC.codigo == acc.codigo).first()
        if not existing:
            db.add(acc)
    db.flush()
    return accounts


# ─── Test ORM Models ─────────────────────────────────────────────


class TestOrmModels:
    """Test that ORM models can be created and retrieved."""

    def test_create_tercero(self, db):
        tercero = Tercero(
            nit="900123456",
            razon_social="Empresa de Prueba SAS",
            tipo=TerceroTipo.PROVEEDOR,
        )
        db.add(tercero)
        db.flush()

        found = db.query(Tercero).filter(Tercero.nit == "900123456").first()
        assert found is not None
        assert found.razon_social == "Empresa de Prueba SAS"
        assert found.tipo == TerceroTipo.PROVEEDOR

    def test_create_cuenta_puc(self, db):
        cuenta = CuentaPUC(
            codigo="999999",
            nombre="Cuenta de Prueba",
            clase=5,
            naturaleza=NaturalezaCuenta.DEBITO,
            activa=True,
        )
        db.add(cuenta)
        db.flush()

        found = db.query(CuentaPUC).filter(CuentaPUC.codigo == "999999").first()
        assert found is not None
        assert found.nombre == "Cuenta de Prueba"

    def test_create_ingest_job(self, db):
        job = IngestJob(
            id="test_ing_001",
            file_name="factura_test.pdf",
            status=IngestStatus.PENDING_PROCESSING,
        )
        db.add(job)
        db.flush()

        found = db.query(IngestJob).filter(IngestJob.id == "test_ing_001").first()
        assert found is not None
        assert found.file_name == "factura_test.pdf"
        assert found.status == IngestStatus.PENDING_PROCESSING

    def test_create_full_transaction_chain(self, db):
        """Test creating the full chain: IngestJob → TransactionPending → TransactionPosted → JournalEntryLine."""
        # IngestJob
        ingest = IngestJob(
            id="test_chain_001", file_name="chain.pdf", status=IngestStatus.PROCESSING
        )
        db.add(ingest)
        db.flush()

        # TransactionPending
        pending = TransactionPending(
            id="test_txn_001",
            ingest_id=ingest.id,
            fecha=datetime.now(timezone.utc),
            total=Decimal("1500000.00"),
            nit_emisor="900111222",
            status=TransactionStatus.PENDING,
        )
        db.add(pending)
        db.flush()

        # TransactionPosted
        posted = TransactionPosted(
            id="test_posted_001",
            transaction_pending_id=pending.id,
            cuenta_puc="519595",
            retefuente=Decimal("150000.00"),
            iva=Decimal("285000.00"),
            neto_a_pagar=Decimal("1350000.00"),
            status=TransactionStatus.POSTED,
        )
        db.add(posted)
        db.flush()

        # JournalEntryLine
        line = JournalEntryLine(
            transaction_posted_id=posted.id,
            fecha=datetime.now(timezone.utc),
            cuenta_puc="519595",
            cuenta_nombre="Gastos Diversos",
            debito=Decimal("1215000.00"),
            credito=Decimal("0"),
        )
        db.add(line)
        db.flush()

        # Verify relationships
        found_posted = (
            db.query(TransactionPosted)
            .filter(TransactionPosted.id == "test_posted_001")
            .first()
        )
        assert found_posted is not None
        assert found_posted.transaction_pending.ingest_id == "test_chain_001"
        assert len(found_posted.journal_lines) == 1


# ─── Test db_service CRUD ────────────────────────────────────────


class TestDbService:
    """Test the db_service repository functions."""

    def test_create_process_job(self, db):
        """create_process_job returns a ProcessJob with correct id prefix and status."""
        job = db_service.create_ingest_job(db, "proc_test.pdf")
        proc = db_service.create_process_job(db, job.id)
        assert proc.id.startswith("proc_")
        assert proc.status == ProcessStatus.QUEUED
        assert proc.ingest_id == job.id

    def test_create_and_get_ingest_job(self, db):
        job = db_service.create_ingest_job(
            db, "test_service.pdf", "/tmp/test_service.pdf"
        )
        assert job.id.startswith("ing_")
        assert job.status == IngestStatus.PENDING_PROCESSING

        found = db_service.get_ingest_job(db, job.id)
        assert found is not None
        assert found.file_name == "test_service.pdf"

    def test_update_ingest_job(self, db):
        job = db_service.create_ingest_job(db, "update_test.pdf")
        updated = db_service.update_ingest_job(
            db,
            job.id,
            IngestStatus.COMPLETED,
            raw_preview={"nit": "123", "total": "1000"},
        )
        assert updated.status == IngestStatus.COMPLETED
        assert updated.raw_preview["nit"] == "123"
        assert updated.completed_at is not None

    def test_create_transaction_pending(self, db):
        job = db_service.create_ingest_job(db, "txn_test.pdf")
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            fecha=datetime.now(timezone.utc),
            nit_emisor="900555666",
            total=Decimal("2500000"),
            descripcion="Compra de suministros",
            items=[{"item": "Papel", "cantidad": 10, "valor": 250000}],
        )
        assert txn.id.startswith("txn_")
        assert txn.total == Decimal("2500000")

    def test_get_transactions_by_status(self, db):
        job = db_service.create_ingest_job(db, "status_test.pdf")
        db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            total=Decimal("100"),
        )
        pending = db_service.get_transactions_by_status(db, TransactionStatus.PENDING)
        assert len(pending) >= 1

    def test_create_transaction_posted(self, db, sample_puc):
        job = db_service.create_ingest_job(db, "posted_test.pdf")
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            total=Decimal("1000000"),
        )
        posted = db_service.create_transaction_posted(
            db,
            transaction_pending_id=txn.id,
            cuenta_puc="519595",
            puc_descripcion="Gastos Diversos",
            retefuente=Decimal("100000"),
            iva=Decimal("190000"),
            neto_a_pagar=Decimal("900000"),
        )
        assert posted.id.startswith("posted_")
        assert posted.cuenta_puc == "519595"

        # Verify pending was updated
        refreshed = (
            db.query(TransactionPending).filter(TransactionPending.id == txn.id).first()
        )
        assert refreshed.status == TransactionStatus.POSTED

    def test_validate_puc(self, db, sample_puc):
        found = db_service.validate_puc_exists(db, "519595")
        assert found is not None
        assert found.nombre == "Gastos Diversos"

        not_found = db_service.validate_puc_exists(db, "000000")
        assert not_found is None

    def test_search_puc(self, db, sample_puc):
        results = db_service.search_puc(db, "proveedor")
        assert any(r.codigo == "220505" for r in results)


# ─── Test Journal Entries & Accounting Books ─────────────────────


class TestAccountingBooks:
    """Test journal entry creation and accounting book queries."""

    @pytest.fixture
    def posted_with_entries(self, db, sample_puc):
        """Create a complete transaction with journal entries."""
        job = db_service.create_ingest_job(db, "books_test.pdf")
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            fecha=datetime(2025, 3, 15, tzinfo=timezone.utc),
            nit_emisor="900777888",
            total=Decimal("1190000"),
            descripcion="Servicio consultoría contable",
        )
        posted = db_service.create_transaction_posted(
            db,
            transaction_pending_id=txn.id,
            cuenta_puc="519595",
            puc_descripcion="Gastos Diversos",
            retefuente=Decimal("110000"),
            iva=Decimal("190000"),
            neto_a_pagar=Decimal("1080000"),
        )

        # Create journal entries (partida doble)
        entries = [
            {
                "fecha": datetime(2025, 3, 15, tzinfo=timezone.utc),
                "comprobante": "CE-001",
                "cuenta": "519595",
                "descripcion": "Gastos Diversos",
                "tercero_nit": "900777888",
                "detalle": "Servicio consultoría",
                "debito": "1000000",
                "credito": "0",
            },
            {
                "fecha": datetime(2025, 3, 15, tzinfo=timezone.utc),
                "comprobante": "CE-001",
                "cuenta": "240802",
                "descripcion": "IVA Descontable",
                "tercero_nit": "900777888",
                "detalle": "IVA consultoría",
                "debito": "190000",
                "credito": "0",
            },
            {
                "fecha": datetime(2025, 3, 15, tzinfo=timezone.utc),
                "comprobante": "CE-001",
                "cuenta": "220505",
                "descripcion": "Proveedores Nacionales",
                "tercero_nit": "900777888",
                "detalle": "CxP consultoría",
                "debito": "0",
                "credito": "1080000",
            },
            {
                "fecha": datetime(2025, 3, 15, tzinfo=timezone.utc),
                "comprobante": "CE-001",
                "cuenta": "240815",
                "descripcion": "Retefuente Servicios",
                "tercero_nit": "900777888",
                "detalle": "Retefuente consultoría",
                "debito": "0",
                "credito": "110000",
            },
        ]

        lines = db_service.create_journal_entry_lines(db, posted.id, entries)
        return posted, lines

    def test_journal_entries_partida_doble(self, db, posted_with_entries):
        """Verify that debits == credits (fundamental accounting equation)."""
        posted, lines = posted_with_entries

        total_debito = sum(line.debito for line in lines)
        total_credito = sum(line.credito for line in lines)

        assert total_debito == total_credito, (
            f"Partida doble violation: D={total_debito} != C={total_credito}"
        )
        assert total_debito == Decimal("1190000")

    def test_libro_diario(self, db, posted_with_entries):
        """Daily journal returns entries in chronological order."""
        lines = db_service.get_daily_journal(
            db,
            start_date=datetime(2025, 3, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 3, 31, tzinfo=timezone.utc),
        )
        assert len(lines) >= 4
        # Verify order
        for i in range(len(lines) - 1):
            assert lines[i].fecha <= lines[i + 1].fecha

    def test_libro_mayor(self, db, posted_with_entries):
        """General ledger aggregates by account."""
        mayor = db_service.get_general_ledger(
            db,
            start_date=datetime(2025, 3, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 3, 31, tzinfo=timezone.utc),
        )
        assert len(mayor) >= 1

        # Find the expense account
        gasto = next((m for m in mayor if m["account"] == "519595"), None)
        assert gasto is not None
        assert gasto["total_debit"] == 1000000.0

    def test_libro_auxiliar(self, db, posted_with_entries):
        """Subsidiary journal returns detail for a specific account."""
        lines = db_service.get_subsidiary_journal(
            db,
            "519595",
            start_date=datetime(2025, 3, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 3, 31, tzinfo=timezone.utc),
        )
        assert len(lines) >= 1
        assert all(line.cuenta_puc == "519595" for line in lines)

    def test_balance_general(self, db, posted_with_entries):
        """Balance sheet calculates correctly from journal entries."""
        balance = db_service.get_balance_sheet(
            db,
            cutoff_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        assert "assets" in balance
        assert "liabilities" in balance
        assert "equity" in balance
        assert "is_balanced" in balance
        # The fixture entries balance perfectly (Assets = Liabilities + Equity + Net Profit)
        assert balance["is_balanced"] is True

    def test_balance_general_unbalanced(self, db, sample_puc):
        """is_balanced is False when journal entries do not balance across account classes."""
        job = db_service.create_ingest_job(db, "unbalanced_test.pdf")
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            fecha=datetime(2025, 5, 1, tzinfo=timezone.utc),
            total=Decimal("1000000"),
        )
        posted = db_service.create_transaction_posted(
            db,
            transaction_pending_id=txn.id,
            cuenta_puc="110505",
        )

        # Intentionally unbalanced: debit class 1 (assets) with no matching credit in class 2/3
        entries = [
            {
                "fecha": datetime(2025, 5, 1, tzinfo=timezone.utc),
                "cuenta": "110505",  # class 1 — assets (debit-nature)
                "descripcion": "Caja General",
                "debito": "1000000",
                "credito": "0",
            },
            {
                "fecha": datetime(2025, 5, 1, tzinfo=timezone.utc),
                "cuenta": "519595",  # class 5 — expenses (debit-nature)
                "descripcion": "Gastos Diversos",
                "debito": "500000",
                "credito": "0",
            },
        ]
        db_service.create_journal_entry_lines(db, posted.id, entries)

        balance = db_service.get_balance_sheet(
            db,
            cutoff_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        # assets=1_000_000, liabilities=0, equity=0, net_profit=-500_000
        # 1_000_000 != 0 + 0 + (-500_000) → is_balanced must be False
        assert balance["is_balanced"] is False

    def test_balance_general_receivable_credit_balance_reclassified_to_liabilities(
        self, db, sample_puc
    ):
        """A class-1 account (cuentas por cobrar) that nets CREDIT — e.g. a
        cobro posted before its originating factura ever debited the
        receivable — is an anticipo de cliente. It must land in liabilities,
        not present as a negative assets total."""
        job = db_service.create_ingest_job(db, "credit_receivable_test.pdf")
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            fecha=datetime(2025, 5, 1, tzinfo=timezone.utc),
            total=Decimal("500000"),
        )
        posted = db_service.create_transaction_posted(
            db,
            transaction_pending_id=txn.id,
            cuenta_puc="130505",
        )
        entries = [
            {
                "fecha": datetime(2025, 5, 1, tzinfo=timezone.utc),
                "cuenta": "111005",  # class 1 — bancos (debit-nature)
                "descripcion": "Consignación cobro",
                "debito": "500000",
                "credito": "0",
            },
            {
                "fecha": datetime(2025, 5, 1, tzinfo=timezone.utc),
                "cuenta": "130505",  # class 1 — cuentas por cobrar, credit-only
                "descripcion": "Cobro sin factura previa",
                "debito": "0",
                "credito": "500000",
            },
        ]
        db_service.create_journal_entry_lines(db, posted.id, entries)

        balance = db_service.get_balance_sheet(
            db,
            cutoff_date=datetime(2025, 12, 31, tzinfo=timezone.utc),
        )
        # 111005 (+500000) and 130505 (-500000) net to zero on the assets
        # side; the 130505 credit balance must reclassify into liabilities
        # instead of leaving assets at 0 while masking a -500000/+500000 mix.
        assert balance["assets"] == 500000.0
        assert balance["liabilities"] == 500000.0


# ─── Test Duplicate Detection ────────────────────────────────────


class TestDuplicateDetection:
    """Test duplicate transaction detection."""

    def test_detect_duplicate(self, db):
        job = db_service.create_ingest_job(db, "dup_test.pdf")
        fecha = datetime(2025, 6, 15, tzinfo=timezone.utc)

        # Create first transaction
        db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            fecha=fecha,
            nit_emisor="900111222",
            total=Decimal("500000"),
        )

        # Check for duplicates with same NIT/amount/date
        dups = db_service.check_duplicates(
            db,
            issuer_nit="900111222",
            total=Decimal("500000"),
            date=fecha,
            days_window=3,
        )
        assert len(dups) >= 1

    def test_no_false_duplicate(self, db):
        """Different NIT should not trigger duplicate."""
        job = db_service.create_ingest_job(db, "no_dup.pdf")
        fecha = datetime(2025, 6, 20, tzinfo=timezone.utc)

        db_service.create_transaction_pending(
            db,
            ingest_id=job.id,
            fecha=fecha,
            nit_emisor="900111222",
            total=Decimal("500000"),
        )

        dups = db_service.check_duplicates(
            db,
            issuer_nit="800999888",  # Different NIT
            total=Decimal("500000"),
            date=fecha,
        )
        assert len(dups) == 0


# ─── Test Audit Log ──────────────────────────────────────────────


class TestAuditLog:
    """Test immutable audit trail."""

    def test_audit_log_created_on_ingest(self, db):
        """Creating an ingest job should automatically create an audit log."""
        job = db_service.create_ingest_job(db, "audit_test.pdf")

        logs = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_id == job.id,
                AuditLog.action == "ingest_created",
            )
            .all()
        )
        assert len(logs) >= 1
        assert logs[0].entity_type == "ingest"

    def test_audit_log_created_on_process_job(self, db):
        """Creating a process job should atomically create an audit log entry."""
        ingest = db_service.create_ingest_job(db, "proc_audit_test.pdf")
        proc = db_service.create_process_job(db, ingest.id)

        logs = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_id == proc.id,
                AuditLog.action == "process_created",
            )
            .all()
        )
        assert len(logs) == 1
        assert logs[0].entity_type == "process"
        assert logs[0].details["ingest_id"] == ingest.id

    def test_process_job_commit_false_rollback(self, db):
        """When commit=False, rolling back removes both the ProcessJob and its audit log."""
        ingest = db_service.create_ingest_job(db, "proc_rollback_test.pdf")
        proc = db_service.create_process_job(db, ingest.id, commit=False)
        proc_id = proc.id

        db.rollback()

        assert db.query(ProcessJob).filter(ProcessJob.id == proc_id).first() is None
        assert db.query(AuditLog).filter(AuditLog.entity_id == proc_id).count() == 0


# ─── Test Tercero ────────────────────────────────────────────────


class TestTercero:
    """Test tercero (business partner) operations."""

    def test_get_or_create_tercero(self, db):
        # Create new
        t1 = db_service.get_or_create_third_party(db, "555666777", "Empresa Nueva")
        assert t1.nit == "555666777"

        # Get existing (same NIT)
        t2 = db_service.get_or_create_third_party(db, "555666777", "Otro Nombre")
        assert t2.id == t1.id  # Same record
        assert t2.razon_social == "Empresa Nueva"  # Original name kept


# ─── Test Atomic Persistence ──────────────────────────────────────


class TestAtomicPersistence:
    """Test that commit=False helpers participate in the caller's transaction."""

    def test_rollback_undoes_all_partial_writes(self, db):
        """
        When service helpers are called with commit=False and the transaction
        is rolled back, no partial rows (IngestJob, TransactionPending, or
        AuditLog) should remain in the database.
        """
        job = db_service.create_ingest_job(db, "atomic_rollback.pdf", commit=False)
        job_id = job.id
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job_id,
            total=Decimal("750000"),
            commit=False,
        )
        txn_id = txn.id

        # Simulate a failure before committing
        db.rollback()

        # All staged writes must be gone
        assert db_service.get_ingest_job(db, job_id) is None
        assert (
            db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
            is None
        )
        # Audit logs for these entities must also be rolled back
        assert db.query(AuditLog).filter(AuditLog.entity_id == job_id).count() == 0

    def test_commit_false_followed_by_explicit_commit_persists_all(self, db):
        """
        When service helpers are called with commit=False and the caller
        issues a single db.commit(), all writes persist atomically.
        """
        job = db_service.create_ingest_job(db, "atomic_commit.pdf", commit=False)
        job_id = job.id
        txn = db_service.create_transaction_pending(
            db,
            ingest_id=job_id,
            total=Decimal("250000"),
            commit=False,
        )
        txn_id = txn.id

        # Caller commits everything in one shot
        db.commit()

        assert db_service.get_ingest_job(db, job_id) is not None
        assert (
            db.query(TransactionPending).filter(TransactionPending.id == txn_id).first()
            is not None
        )
        # Audit logs must also be present
        assert (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_id == job_id,
                AuditLog.action == "ingest_created",
            )
            .count()
            >= 1
        )


# ─── Test ParserMode & IngestStatus ──────────────────────────────


class TestParserModeAndCancelledStatus:
    """Test ParserMode enum and CANCELLED status on IngestJob."""

    def test_ingest_job_has_parser_mode(self, db):
        job = IngestJob(
            id="test_parser_001",
            file_name="parser_test.pdf",
            status=IngestStatus.PENDING_PROCESSING,
            parser_mode=ParserMode.PREMIUM,
        )
        db.add(job)
        db.flush()

        found = db.query(IngestJob).filter(IngestJob.id == "test_parser_001").first()
        assert found is not None
        assert found.parser_mode == "premium"

    def test_ingest_job_defaults_to_fast(self, db):
        job = IngestJob(
            id="test_parser_002",
            file_name="parser_default.pdf",
            status=IngestStatus.PENDING_PROCESSING,
        )
        db.add(job)
        db.flush()

        found = db.query(IngestJob).filter(IngestJob.id == "test_parser_002").first()
        assert found is not None
        assert found.parser_mode == "fast"

    def test_ingest_status_has_cancelled(self):
        assert IngestStatus.CANCELLED == "CANCELLED"
