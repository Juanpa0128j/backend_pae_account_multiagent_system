"""
Feature tests for soft-delete / restore on core entities.

Covers:
  1. TransactionPosted — soft_delete sets deleted_at; list excludes it
  2. ChatSession — same pattern
  3. CuentaPUC — same pattern
  4. UserCompany — same pattern
  5. UserCompanyResponse includes razon_social field
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://pae_user:password@localhost:5432/pae_accounting",
)

# ─── DB fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def engine():
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
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

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


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_ingest_job(db, job_id: str | None = None):
    from app.models.database import IngestJob

    job = IngestJob(
        id=job_id or f"ingest-{uuid.uuid4()}",
        file_name="test.pdf",
    )
    db.add(job)
    db.flush()
    return job


def _make_transaction_pending(db, ingest_id: str, pending_id: str | None = None):
    from app.models.database import TransactionPending

    row = TransactionPending(
        id=pending_id or f"pending-{uuid.uuid4()}",
        ingest_id=ingest_id,
        company_nit="900000001",
    )
    db.add(row)
    db.flush()
    return row


def _make_transaction_posted(db, pending_id: str, posted_id: str | None = None):
    from app.models.database import TransactionPosted

    row = TransactionPosted(
        id=posted_id or f"posted-{uuid.uuid4()}",
        transaction_pending_id=pending_id,
        company_nit="900000001",
        cuenta_puc="519595",
    )
    db.add(row)
    db.flush()
    return row


def _make_company_settings(db, nit: str, nombre: str = "Empresa Test S.A.S"):
    from app.models.database import CompanySettings

    existing = db.query(CompanySettings).filter(CompanySettings.nit == nit).first()
    if existing:
        return existing
    row = CompanySettings(nit=nit, nombre=nombre)
    db.add(row)
    db.flush()
    return row


# ─── Test 1: TransactionPosted soft-delete ───────────────────────────────────


def test_soft_delete_transaction_posted(db):
    """soft_delete_transaction_posted sets deleted_at; list excludes it."""
    from app.services import db_service

    ingest = _make_ingest_job(db)
    pending = _make_transaction_pending(db, ingest.id)
    posted = _make_transaction_posted(db, pending.id)
    posted_id = posted.id

    # Confirm it appears in a basic query before deletion
    result = (
        db.query(posted.__class__)
        .filter(
            posted.__class__.id == posted_id,
            posted.__class__.deleted_at.is_(None),
        )
        .first()
    )
    assert result is not None, "Record should exist before soft-delete"

    # Soft-delete
    deleted = db_service.soft_delete_transaction_posted(db, posted_id)
    assert deleted is True

    # Verify deleted_at is set
    db.refresh(posted)
    assert posted.deleted_at is not None

    # List query with deleted_at filter should exclude it
    from app.models.database import TransactionPosted

    active = (
        db.query(TransactionPosted)
        .filter(
            TransactionPosted.id == posted_id,
            TransactionPosted.deleted_at.is_(None),
        )
        .first()
    )
    assert active is None, "Soft-deleted record should not appear in active list"


# ─── Test 2: ChatSession soft-delete ────────────────────────────────────────


def test_soft_delete_chat_session(db):
    """soft_delete_chat_session sets deleted_at; list_chat_sessions excludes it; restore returns it."""
    from app.models.database import ChatSession
    from app.services import db_service

    session_id = f"chat-{uuid.uuid4()}"
    chat = ChatSession(id=session_id, company_nit="900000002", title="Test Session")
    db.add(chat)
    db.flush()

    # Should appear in list before deletion
    before = db_service.list_chat_sessions(db, company_nit="900000002")
    assert any(s.id == session_id for s in before), (
        "Session should be listed before delete"
    )

    # Soft-delete
    deleted = db_service.soft_delete_chat_session(db, session_id)
    assert deleted is True

    # Verify deleted_at is set
    db.refresh(chat)
    assert chat.deleted_at is not None

    # list_chat_sessions should exclude it
    after = db_service.list_chat_sessions(db, company_nit="900000002")
    assert not any(s.id == session_id for s in after), (
        "Deleted session should not appear in list"
    )

    # Restore
    restored = db_service.restore_chat_session(db, session_id)
    assert restored is not None
    assert restored.deleted_at is None

    # Back in list after restore
    back = db_service.list_chat_sessions(db, company_nit="900000002")
    assert any(s.id == session_id for s in back), (
        "Restored session should appear in list"
    )


# ─── Test 3: CuentaPUC soft-delete ──────────────────────────────────────────


def test_soft_delete_puc(db):
    """soft_delete_cuenta_puc sets deleted_at; PUC queries exclude it; restore returns it."""
    from app.models.database import CuentaPUC, NaturalezaCuenta
    from app.services import db_service

    codigo = f"9{uuid.uuid4().hex[:5]}"
    puc = CuentaPUC(
        codigo=codigo,
        nombre="Cuenta Test Soft Delete",
        clase=5,
        naturaleza=NaturalezaCuenta.DEBITO,
    )
    db.add(puc)
    db.flush()

    # Confirm exists with deleted_at=None
    existing = (
        db.query(CuentaPUC)
        .filter(CuentaPUC.codigo == codigo, CuentaPUC.deleted_at.is_(None))
        .first()
    )
    assert existing is not None, "PUC should exist before soft-delete"

    # Soft-delete
    deleted = db_service.soft_delete_cuenta_puc(db, codigo)
    assert deleted is True

    # Verify deleted_at set
    db.refresh(puc)
    assert puc.deleted_at is not None

    # Active query should exclude it
    excluded = (
        db.query(CuentaPUC)
        .filter(CuentaPUC.codigo == codigo, CuentaPUC.deleted_at.is_(None))
        .first()
    )
    assert excluded is None, "Soft-deleted PUC should not appear in active query"

    # Restore
    restored = db_service.restore_cuenta_puc(db, codigo)
    assert restored is not None
    assert restored.deleted_at is None

    # Back in active query
    back = (
        db.query(CuentaPUC)
        .filter(CuentaPUC.codigo == codigo, CuentaPUC.deleted_at.is_(None))
        .first()
    )
    assert back is not None, "Restored PUC should appear in active query"


# ─── Test 4: UserCompany soft-delete ────────────────────────────────────────


def test_soft_delete_company_membership(db):
    """soft_delete_user_company sets deleted_at; list_user_companies excludes it; restore returns it."""
    from app.models.database import UserCompany
    from app.services import db_service

    nit = "900000003"
    user_id = f"user-{uuid.uuid4()}"

    _make_company_settings(db, nit)

    membership = UserCompany(user_id=user_id, company_nit=nit)
    db.add(membership)
    db.flush()

    # Should appear in list before deletion
    before = db_service.list_user_companies(db, company_nit=nit)
    assert any(m.user_id == user_id for m in before), (
        "Membership should be listed before delete"
    )

    # Soft-delete
    deleted = db_service.soft_delete_user_company(db, user_id, nit)
    assert deleted is True

    # Verify deleted_at is set
    db.refresh(membership)
    assert membership.deleted_at is not None

    # list_user_companies should exclude it
    after = db_service.list_user_companies(db, company_nit=nit)
    assert not any(m.user_id == user_id for m in after), (
        "Deleted membership should not appear in list"
    )

    # Restore
    restored = db_service.restore_user_company(db, user_id, nit)
    assert restored is not None
    assert restored.deleted_at is None

    # Back in list after restore
    back = db_service.list_user_companies(db, company_nit=nit)
    assert any(m.user_id == user_id for m in back), (
        "Restored membership should appear in list"
    )


# ─── Test 5: UserCompanyResponse includes razon_social ──────────────────────


def test_user_company_response_includes_razon_social():
    """UserCompanyResponse schema has razon_social field and accepts None or str value."""
    from datetime import datetime, timezone

    from app.api.v1.auth import UserCompanyResponse

    # With razon_social populated
    resp_with = UserCompanyResponse(
        user_id="user-123",
        company_nit="900000004",
        joined_at=datetime.now(timezone.utc),
        razon_social="Empresa Ejemplo S.A.S.",
    )
    assert resp_with.razon_social == "Empresa Ejemplo S.A.S."

    # Without razon_social (defaults to None)
    resp_without = UserCompanyResponse(
        user_id="user-456",
        company_nit="900000005",
        joined_at=datetime.now(timezone.utc),
    )
    assert resp_without.razon_social is None

    # Field exists in model schema
    fields = UserCompanyResponse.model_fields
    assert "razon_social" in fields
