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
from app.services.tax_declaration_service import _build_f350, build_draft_from_catalog
from app.services.dian_forms import get_catalog


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


def _draft(**kw):
    """Run _build_f350 and project through the official F350 catalog."""
    computed, warnings = _build_f350(**kw)
    fields = build_draft_from_catalog(get_catalog("F350"), computed)
    return {f.renglon: f for f in fields}, warnings


# Official retención casillas per (categoria, aplica_a):
#   compras PJ → 49, compras PN → 102, honorarios PJ → 42, salarios PN → 93.
#   Unmapped concepts (hidrocarburos, pes) → "Otros pagos" 54 (PJ/AMB) / 108 (PN).


# ─── Concepto-driven casillas ──────────────────────────────────────────────


def test_f350_compras_pj_maps_to_casilla_49(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=100.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["49"].value == 100.0


def test_f350_mix_pj_pn_maps_to_49_and_102(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    _post_retencion(db, "t2", "compras_pn", "60")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=160.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["49"].value == 100.0  # compras PJ
    assert f["102"].value == 60.0  # compras PN


def test_f350_hidrocarburos_routed_to_otros_54(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "hidrocarburos", "200")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=200.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["54"].value == 200.0  # otros pagos (concepto sin casilla propia)
    assert f["49"].value == 0.0  # compras_pj sin movimiento


def test_f350_pes_routed_to_otros_54(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "pes_svcs_dig", "300")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=300.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["54"].value == 300.0


def test_f350_reteica_excluded(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "reteica", "50", cuenta_credito="236805")
    f, _ = _draft(
        ledger=_ledger_2365_2368(reteica=50.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    # ReteICA es municipal → excluida del F350; total renta = 0.
    assert f["130"].value == 0.0


def test_f350_unclassified_emits_warning(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", None, "75")
    f, warnings = _draft(
        ledger=_ledger_2365_2368(retefuente=75.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    # El gap sin clasificar va a "Otros pagos" (54) y emite advertencia.
    assert f["54"].value == pytest.approx(75.0)
    assert any(w.field == "54" for w in warnings)


def test_f350_total_130_sums_concepto_casillas(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    _post_retencion(db, "t2", "honorarios_pj", "200")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=300.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    # 130 (total retenciones renta) = compras PJ (49) + honorarios PJ (42)
    assert f["130"].value == pytest.approx(300.0)


def test_f350_zero_amount_concept_skipped(db):
    _seed_standard_concepts(db)
    f, _ = _draft(
        ledger=_ledger_2365_2368(),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["49"].value == 0.0
    assert f["54"].value == 0.0


def test_f350_inactive_concept_skipped(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    db_service.soft_delete_tax_concept(db, "compras_pj")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=100.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["49"].value == 0.0


def test_f350_legacy_no_db_renders_full_catalog(db):
    f, warnings = _draft(
        ledger=_ledger_2365_2368(retefuente=100.0),
        settings=_settings(),
        db=None,
        company_nit=None,
    )
    # Sanciones (137) y "menos retenciones en exceso" (129) son manuales y
    # siempre aparecen en el catálogo.
    assert f["137"].requires_review is True
    assert "129" in f


def test_f350_sanciones_casilla_137_manual(db):
    _seed_standard_concepts(db)
    f, _ = _draft(
        ledger=_ledger_2365_2368(),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["137"].requires_review is True


def test_f350_pj_pn_land_in_distinct_casillas(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    _post_retencion(db, "t2", "compras_pn", "60")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=160.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    assert f["49"].value == 100.0 and f["102"].value == 60.0


def test_f350_base_estimated_from_tarifa(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=100.0),
        settings=_settings(),
        db=db,
        company_nit="900",
    )
    # Base estimada = 100 / 2.5% = 4000 → casilla 36 (compras PJ base)
    assert f["36"].value == pytest.approx(4000.0)


def test_f350_period_filter_excludes_outside_dates(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=100.0),
        settings=_settings(),
        db=db,
        company_nit="900",
        period_start=datetime(2099, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2099, 12, 31, tzinfo=timezone.utc),
    )
    assert f["49"].value == 0.0


def test_f350_company_filter_isolates_tenants(db):
    _seed_standard_concepts(db)
    _post_retencion(db, "t1", "compras_pj", "100")
    f, _ = _draft(
        ledger=_ledger_2365_2368(retefuente=100.0),
        settings=_settings(),
        db=db,
        company_nit="OTHER_NIT",
    )
    assert f["49"].value == 0.0
