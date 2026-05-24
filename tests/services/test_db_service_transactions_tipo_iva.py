"""DB roundtrip tests for transactions_posted.tipo_iva.

These tests rely on the in-memory SQLite fixture used elsewhere in the suite
for repository-layer checks. They verify that create_transaction_posted
persists tipo_iva and that get_revenue_by_tipo_iva groups credits correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import (
    Base,
    IngestJob,
    IngestStatus,
    TransactionPending,
    TransactionStatus,
)
from app.services import db_service
from app.services.tax_constants import (
    TIPO_IVA_EXCLUIDO,
    TIPO_IVA_GRAVADO_19,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _pending(db, txn_id: str = "p1") -> TransactionPending:
    ingest = db.query(IngestJob).filter(IngestJob.id == "ig1").first()
    if not ingest:
        ingest = IngestJob(
            id="ig1", file_name="test.pdf", status=IngestStatus.COMPLETED
        )
        db.add(ingest)
        db.commit()
    p = TransactionPending(
        id=txn_id,
        ingest_id="ig1",
        company_nit="900",
        fecha=datetime.now(timezone.utc),
        nit_emisor="800",
        descripcion="ingreso",
        total=Decimal("1000"),
        status=TransactionStatus.PENDING,
    )
    db.add(p)
    db.commit()
    return p


def test_create_transaction_posted_persists_tipo_iva(db):
    _pending(db, "p1")
    posted = db_service.create_transaction_posted(
        db,
        transaction_pending_id="p1",
        cuenta_puc="4135",
        tipo_iva=TIPO_IVA_GRAVADO_19,
        company_nit="900",
    )
    assert posted.tipo_iva == TIPO_IVA_GRAVADO_19


def test_create_transaction_posted_allows_null_tipo_iva(db):
    _pending(db, "p2")
    posted = db_service.create_transaction_posted(
        db, transaction_pending_id="p2", cuenta_puc="4135", company_nit="900"
    )
    assert posted.tipo_iva is None


def test_create_transaction_posted_rejects_invalid_tipo_iva(db):
    _pending(db, "p3")
    with pytest.raises(ValueError):
        db_service.create_transaction_posted(
            db,
            transaction_pending_id="p3",
            cuenta_puc="4135",
            tipo_iva="bogus_value",
            company_nit="900",
        )


def test_get_revenue_by_tipo_iva_groups_by_tipo(db):
    # 2 posted with gravado_19, 1 with excluido
    for i, tipo in enumerate(
        [TIPO_IVA_GRAVADO_19, TIPO_IVA_GRAVADO_19, TIPO_IVA_EXCLUIDO]
    ):
        _pending(db, f"p{i}")
        posted = db_service.create_transaction_posted(
            db,
            transaction_pending_id=f"p{i}",
            cuenta_puc="4135",
            tipo_iva=tipo,
            company_nit="900",
        )
        db_service.create_journal_entry_lines(
            db,
            transaction_posted_id=posted.id,
            entries=[
                {
                    "fecha": datetime.now(timezone.utc),
                    "cuenta": "4135",
                    "credito": "1000",
                    "debito": "0",
                }
            ],
            company_nit="900",
        )
    out = db_service.get_revenue_by_tipo_iva(db, company_nit="900")
    assert out.get(TIPO_IVA_GRAVADO_19) == 2000.0
    assert out.get(TIPO_IVA_EXCLUIDO) == 1000.0


def test_get_revenue_by_tipo_iva_null_grouped_as_sin_clasificar(db):
    _pending(db, "p10")
    posted = db_service.create_transaction_posted(
        db, transaction_pending_id="p10", cuenta_puc="4135", company_nit="900"
    )
    db_service.create_journal_entry_lines(
        db,
        transaction_posted_id=posted.id,
        entries=[
            {
                "fecha": datetime.now(timezone.utc),
                "cuenta": "4135",
                "credito": "500",
                "debito": "0",
            }
        ],
        company_nit="900",
    )
    out = db_service.get_revenue_by_tipo_iva(db, company_nit="900")
    assert out.get("sin_clasificar") == 500.0
