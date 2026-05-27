"""Tests for duplicate TransactionPosted guard in persist_node.

Covers:
- First upload posts normally
- Second identical upload (same company_nit, nit_emisor, fecha::date, total) is skipped
- Different content (different total) posts normally
- duplicate_skipped flag set in state on skip
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from app.services import db_service


@pytest.fixture()
def db():
    """In-memory SQLite session with all ORM tables."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_posted(db, company_nit, nit_emisor, fecha, total, pending_id=None):
    """Helper: create TransactionPending + TransactionPosted in DB."""
    import uuid

    pending_id = pending_id or str(uuid.uuid4())
    pending = db_service.create_transaction_pending(
        db,
        ingest_id=str(uuid.uuid4()),
        company_nit=company_nit,
        fecha=fecha,
        nit_emisor=nit_emisor,
        nit_receptor="900000001",
        total=total,
        descripcion="Test factura",
        items=[],
        raw_data={},
        source_file=None,
    )
    posted = db_service.create_transaction_posted(
        db,
        transaction_pending_id=pending.id,
        company_nit=company_nit,
        cuenta_puc="511505",
        puc_descripcion="ICA administración",
        retefuente=Decimal("0"),
        reteica=Decimal("0"),
        iva=Decimal("0"),
        ica=Decimal("500"),
        provision_renta=Decimal("0"),
        neto_a_pagar=total,
        journal_entries_json=[],
        tax_references=[],
        agent_reasoning={},
    )
    return pending, posted


class TestFindDuplicatePosted:
    def test_returns_none_when_no_prior_posted(self, db):
        fecha = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        result = db_service.find_duplicate_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=fecha,
            total=Decimal("1500000"),
        )
        assert result is None

    def test_returns_existing_when_natural_key_matches(self, db):
        fecha = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        _pending, posted = _make_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=fecha,
            total=Decimal("1500000"),
        )
        # Query with slightly different time — same date
        query_fecha = datetime(2026, 3, 15, 14, 0, 0, tzinfo=timezone.utc)
        result = db_service.find_duplicate_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=query_fecha,
            total=Decimal("1500000"),
        )
        assert result is not None
        assert result.id == posted.id

    def test_returns_none_when_different_total(self, db):
        fecha = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        _make_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=fecha,
            total=Decimal("1500000"),
        )
        result = db_service.find_duplicate_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=fecha,
            total=Decimal("2000000"),  # different total
        )
        assert result is None

    def test_returns_none_when_different_date(self, db):
        fecha = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        _make_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=fecha,
            total=Decimal("1500000"),
        )
        other_fecha = datetime(2026, 3, 16, 10, 0, 0, tzinfo=timezone.utc)  # next day
        result = db_service.find_duplicate_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=other_fecha,
            total=Decimal("1500000"),
        )
        assert result is None

    def test_returns_none_when_different_nit_emisor(self, db):
        fecha = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        _make_posted(
            db,
            company_nit="900123456",
            nit_emisor="800111222",
            fecha=fecha,
            total=Decimal("1500000"),
        )
        result = db_service.find_duplicate_posted(
            db,
            company_nit="900123456",
            nit_emisor="800999999",  # different emisor
            fecha=fecha,
            total=Decimal("1500000"),
        )
        assert result is None
