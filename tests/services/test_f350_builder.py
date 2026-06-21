"""F350 builder — Res. DIAN 000031/2024 concepto-discriminated tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

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
from app.services.tax_declaration_service import _build_f350


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


def _settings(**over):
    s = MagicMock()
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _seed_concept(db, code, renglon, aplica_a="AMB", categoria="compras"):
    return db_service.upsert_tax_concept(
        db,
        code=code,
        label=code.replace("_", " ").title(),
        renglon_350=renglon,
        aplica_a=aplica_a,
        categoria=categoria,
        tarifa_default=Decimal("0.025"),
        base_minima_uvt=Decimal("0"),
        art_referencia="test",
    )


def _seed_standard_concepts(db):
    _seed_concept(db, "compras_pj", "25", aplica_a="PJ", categoria="compras")
    _seed_concept(db, "compras_pn", "27", aplica_a="PN", categoria="compras")
    _seed_concept(db, "honorarios_pj", "32", aplica_a="PJ", categoria="honorarios")
    _seed_concept(db, "hidrocarburos", "40", aplica_a="AMB", categoria="hidrocarburos")
    _seed_concept(db, "pes_svcs_dig", "65", aplica_a="AMB", categoria="pes")
    _seed_concept(db, "salarios_383", "50", aplica_a="PN", categoria="salarios")
    _seed_concept(db, "reteica", "76", aplica_a="AMB", categoria="ica")


def _pending(db, txn_id: str) -> TransactionPending:
    ingest = db.query(IngestJob).filter(IngestJob.id == "ig1").first()
    if not ingest:
        ingest = IngestJob(id="ig1", file_name="x.pdf", status=IngestStatus.COMPLETED)
        db.add(ingest)
        db.commit()
    p = TransactionPending(
        id=txn_id,
        ingest_id="ig1",
        company_nit="900",
        fecha=datetime.now(timezone.utc),
        nit_emisor="800",
        descripcion="d",
        total=Decimal("1000"),
        status=TransactionStatus.PENDING,
    )
    db.add(p)
    db.commit()
    return p


def _post_retencion(
    db,
    txn_id: str,
    concepto: str | None,
    monto: str,
    cuenta_credito: str = "236505",
):
    """Create a TransactionPosted + matching journal credit on retención liability."""
    _pending(db, txn_id)
    posted = db_service.create_transaction_posted(
        db,
        transaction_pending_id=txn_id,
        cuenta_puc="5135" if cuenta_credito.startswith("2365") else "511505",
        retefuente=(
            Decimal(monto) if cuenta_credito.startswith("2365") else Decimal("0")
        ),
        reteica=Decimal(monto) if cuenta_credito.startswith("2368") else Decimal("0"),
        company_nit="900",
        concepto_retencion=concepto,
        tipo_persona_emisor="PJ",
    )
    db_service.create_journal_entry_lines(
        db,
        transaction_posted_id=posted.id,
        entries=[
            {
                "fecha": datetime.now(timezone.utc),
                "cuenta": cuenta_credito,
                "credito": monto,
                "debito": "0",
            }
        ],
        company_nit="900",
    )
    return posted


def _ledger_2365_2368(retefuente=0.0, reteica=0.0):
    return [
        {
            "account": "2365",
            "total_debit": 0.0,
            "total_credit": retefuente,
        },
        {
            "account": "2368",
            "total_debit": 0.0,
            "total_credit": reteica,
        },
    ]


def _fields_by_renglon(fields):
    return {f.renglon: f for f in fields}


# ─── Concepto-driven renglones ──────────────────────────────────────────────


def test_f350_compras_pj_emits_renglon_25(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=100.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert "25" in f
    assert f["25"].value == 100.0
    assert "(PJ)" in f["25"].label


def test_f350_mix_pj_pn_emits_two_renglones(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    _post_retencion(db, "t2", "compras_pn", "60")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=160.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert f["25"].value == 100.0
    assert f["27"].value == 60.0


def test_f350_hidrocarburos_renglon_40_not_25(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "hidrocarburos", "200")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=200.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert f["40"].value == 200.0
    assert "25" not in f  # compras_pj has zero monto


def test_f350_pes_renglon_65(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "pes_svcs_dig", "300")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=300.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert f["65"].value == 300.0


def test_f350_reteica_uses_2368_via_concepto(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "reteica", "50", cuenta_credito="236805")
    fields, _ = _build_f350(
        _ledger_2365_2368(reteica=50.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert f["76"].value == 50.0


def test_f350_unclassified_emits_warning(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", None, "75")
    fields, warnings = _build_f350(
        _ledger_2365_2368(retefuente=75.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert "_sin_clasificar" in f
    assert any(w.field == "_sin_clasificar" for w in warnings)


def test_f350_total_renglon_sums_concepto_renglones(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    _post_retencion(db, "t2", "honorarios_pj", "200")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=300.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert f["_total_retenciones"].value == pytest.approx(300.0)


def test_f350_zero_amount_concept_skipped(db):
    _seed_standard_concepts(db)
    fields, _ = _build_f350(
        _ledger_2365_2368(),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert "25" not in f
    assert "40" not in f


def test_f350_inactive_concept_skipped(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    db_service.soft_delete_tax_concept(db, "compras_pj")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=100.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert "25" not in f


def test_f350_legacy_no_db_still_renders_manual_renglones(db):
    fields, warnings = _build_f350(
        _ledger_2365_2368(retefuente=100.0),
        _settings(),
        db=None,
        company_nit=None,
    )
    f = _fields_by_renglon(fields)
    # Manual / sanciones renglones always present (renglón 50 only if nómina data exists).
    assert "75" in f
    assert "97" in f


def test_f350_includes_manual_renglones_75_97(db):
    _seed_standard_concepts(db)
    fields, _ = _build_f350(
        _ledger_2365_2368(),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    # Renglón 50 only present when nómina data exists; 75 and 97 always present.
    assert {"75", "97"} <= set(f)


def test_f350_label_suffix_pj_pn(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    _post_retencion(db, "t2", "compras_pn", "60")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=160.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert "(PJ)" in f["25"].label
    assert "(PN)" in f["27"].label


def test_f350_amb_concept_no_pj_pn_suffix(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "hidrocarburos", "200")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=200.0),
        _settings(),
        db=db,
        company_nit="900",
    )
    f = _fields_by_renglon(fields)
    assert "(PJ)" not in f["40"].label
    assert "(PN)" not in f["40"].label


def test_f350_period_filter_excludes_outside_dates(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=100.0),
        _settings(),
        db=db,
        company_nit="900",
        period_start=datetime(2099, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2099, 12, 31, tzinfo=timezone.utc),
    )
    f = _fields_by_renglon(fields)
    assert "25" not in f


def test_f350_company_filter_isolates_tenants(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    # Query a different tenant — must return zero.
    fields, _ = _build_f350(
        _ledger_2365_2368(retefuente=100.0),
        _settings(),
        db=db,
        company_nit="OTHER_NIT",
    )
    f = _fields_by_renglon(fields)
    assert "25" not in f
