"""Unit tests for db_service TaxConcept helpers (F350 Res. DIAN 000031/2024).

Covers list/get/upsert/soft delete + sum_retencion_by_concepto roundtrip
against the in-memory SQLite fixture.
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
    TaxConcept,
    TransactionPending,
    TransactionStatus,
)
from app.services import db_service


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


def _seed_concept(db, code="compras_pj", **over):
    defaults = dict(
        label="Compras PJ",
        renglon_350="25",
        aplica_a="PJ",
        categoria="compras",
        tarifa_default=Decimal("0.0250"),
        base_minima_uvt=Decimal("27"),
        art_referencia="Art. 392 ET",
    )
    defaults.update(over)
    return db_service.upsert_tax_concept(db, code=code, **defaults)


def test_upsert_inserts_new_row(db):
    row = _seed_concept(db, code="compras_pj")
    assert row.code == "compras_pj"
    assert row.renglon_350 == "25"
    assert row.activo is True


def test_upsert_updates_existing_row(db):
    _seed_concept(db, code="compras_pj", label="A")
    row2 = _seed_concept(db, code="compras_pj", label="B")
    assert row2.label == "B"
    assert db.query(TaxConcept).filter(TaxConcept.code == "compras_pj").count() == 1


def test_list_filters_activo_true_by_default(db):
    _seed_concept(db, code="compras_pj")
    _seed_concept(db, code="hidrocarburos", aplica_a="AMB", categoria="hidrocarburos")
    db_service.soft_delete_tax_concept(db, "hidrocarburos")
    rows = db_service.list_tax_concepts(db)
    codes = {r["code"] for r in rows}
    assert codes == {"compras_pj"}


def test_list_with_activo_none_returns_all(db):
    _seed_concept(db, code="compras_pj")
    _seed_concept(db, code="hidrocarburos", aplica_a="AMB", categoria="hidrocarburos")
    db_service.soft_delete_tax_concept(db, "hidrocarburos")
    rows = db_service.list_tax_concepts(db, activo=None)
    assert {r["code"] for r in rows} == {"compras_pj", "hidrocarburos"}


def test_get_returns_none_for_missing(db):
    assert db_service.get_tax_concept(db, "missing") is None


def test_soft_delete_marks_inactive(db):
    _seed_concept(db, code="compras_pj")
    row = db_service.soft_delete_tax_concept(db, "compras_pj")
    assert row is not None
    assert row.activo is False
    # Returns None when code missing.
    assert db_service.soft_delete_tax_concept(db, "nope") is None


def test_upsert_rejects_invalid_aplica_a(db):
    with pytest.raises(ValueError):
        db_service.upsert_tax_concept(
            db,
            code="bad",
            label="x",
            renglon_350="1",
            aplica_a="ZZ",
            categoria="compras",
        )


def test_sum_retencion_by_concepto_filters_by_code(db):
    _seed_concept(db, code="compras_pj")
    _pending(db, "p1")
    _pending(db, "p2")
    p1 = db_service.create_transaction_posted(
        db,
        transaction_pending_id="p1",
        cuenta_puc="143505",
        retefuente=Decimal("100"),
        company_nit="900",
        concepto_retencion="compras_pj",
        tipo_persona_emisor="PJ",
    )
    p2 = db_service.create_transaction_posted(
        db,
        transaction_pending_id="p2",
        cuenta_puc="143505",
        retefuente=Decimal("50"),
        company_nit="900",
        concepto_retencion="compras_pj",
        tipo_persona_emisor="PJ",
    )
    for posted in (p1, p2):
        db_service.create_journal_entry_lines(
            db,
            transaction_posted_id=posted.id,
            entries=[
                {
                    "fecha": datetime.now(timezone.utc),
                    "cuenta": "236505",
                    "credito": ("100" if posted.id == p1.id else "50"),
                    "debito": "0",
                }
            ],
            company_nit="900",
        )
    total = db_service.sum_retencion_by_concepto(db, "compras_pj", company_nit="900")
    assert total == Decimal("150")


def test_sum_retencion_by_concepto_unknown_code_returns_zero(db):
    assert db_service.sum_retencion_by_concepto(db, "nope") == Decimal("0")


def test_count_unclassified_retenciones(db):
    _pending(db, "p1")
    _pending(db, "p2")
    db_service.create_transaction_posted(
        db,
        transaction_pending_id="p1",
        cuenta_puc="5135",
        retefuente=Decimal("100"),
        company_nit="900",
        concepto_retencion=None,
        tipo_persona_emisor="PJ",
    )
    db_service.create_transaction_posted(
        db,
        transaction_pending_id="p2",
        cuenta_puc="5135",
        retefuente=Decimal("0"),
        company_nit="900",
        concepto_retencion=None,
    )
    assert db_service.count_unclassified_retenciones(db, company_nit="900") == 1
