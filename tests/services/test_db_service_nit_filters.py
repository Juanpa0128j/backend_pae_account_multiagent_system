"""Verify that Reportero analytics queries scope results by company_nit."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import (
    JournalEntryLine,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from app.services import db_service


NIT_A = "900111111"
NIT_B = "900222222"


@pytest.fixture()
def db():
    """In-memory SQLite session with JournalEntryLine rows for two NITs."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    now = datetime.now(timezone.utc).replace(microsecond=0)

    # Posted ancestors required by the JOIN added in get_balance_sheet_for_period.
    # SQLite does not enforce foreign keys, so the FK strings below need not point
    # at real rows — only NOT NULL constraints matter.
    posted_ids = [
        ("tp_A_1", NIT_A, "413595"),
        ("tp_A_2", NIT_A, "413595"),
        ("tp_A_3", NIT_A, "519595"),
        ("tp_B_1", NIT_B, "413595"),
    ]
    pending_rows = [
        TransactionPending(
            id=f"tpend_{tp_id}",
            ingest_id="ig_fake",
            company_nit=nit,
            status=TransactionStatus.POSTED,
        )
        for tp_id, nit, _ in posted_ids
    ]
    posted_rows = [
        TransactionPosted(
            id=tp_id,
            transaction_pending_id=f"tpend_{tp_id}",
            company_nit=nit,
            cuenta_puc=puc,
            status=TransactionStatus.POSTED,
        )
        for tp_id, nit, puc in posted_ids
    ]
    session.add_all(pending_rows + posted_rows)
    session.commit()

    rows = [
        JournalEntryLine(
            transaction_posted_id="tp_A_1",
            fecha=now,
            company_nit=NIT_A,
            cuenta_puc="413595",
            cuenta_nombre="Ingresos A",
            tercero_nit="800000001",
            debito=Decimal("0"),
            credito=Decimal("1000"),
        ),
        JournalEntryLine(
            transaction_posted_id="tp_A_2",
            fecha=now,
            company_nit=NIT_A,
            cuenta_puc="413595",
            cuenta_nombre="Ingresos A",
            tercero_nit="800000001",
            debito=Decimal("0"),
            credito=Decimal("500"),
        ),
        JournalEntryLine(
            transaction_posted_id="tp_A_3",
            fecha=now,
            company_nit=NIT_A,
            cuenta_puc="519595",
            cuenta_nombre="Gastos A",
            tercero_nit="800000002",
            debito=Decimal("300"),
            credito=Decimal("0"),
        ),
        JournalEntryLine(
            transaction_posted_id="tp_B_1",
            fecha=now,
            company_nit=NIT_B,
            cuenta_puc="413595",
            cuenta_nombre="Ingresos B",
            tercero_nit="800000099",
            debito=Decimal("0"),
            credito=Decimal("7777"),
        ),
    ]
    session.add_all(rows)
    session.commit()

    yield session

    session.close()


def test_balance_sheet_for_period_filters_by_nit(db):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)

    result_a = db_service.get_balance_sheet_for_period(
        db, start_date=past, end_date=future, company_nit=NIT_A
    )
    assert result_a["revenue"] == 1500.0
    assert result_a["expenses"] == 300.0

    result_b = db_service.get_balance_sheet_for_period(
        db, start_date=past, end_date=future, company_nit=NIT_B
    )
    assert result_b["revenue"] == 7777.0
    assert result_b["expenses"] == 0.0


def test_balance_sheet_for_period_without_nit_returns_all(db):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    result = db_service.get_balance_sheet_for_period(
        db, start_date=past, end_date=future
    )
    assert result["revenue"] == 1500.0 + 7777.0


def test_top_terceros_filters_by_nit(db):
    top_a = db_service.get_top_terceros(db, company_nit=NIT_A, limit=10)
    nits = {r["nit"] for r in top_a}
    assert nits == {"800000001", "800000002"}
    assert "800000099" not in nits

    top_b = db_service.get_top_terceros(db, company_nit=NIT_B, limit=10)
    assert {r["nit"] for r in top_b} == {"800000099"}


def test_top_terceros_without_nit_returns_all(db):
    top_all = db_service.get_top_terceros(db, limit=10)
    assert {r["nit"] for r in top_all} == {
        "800000001",
        "800000002",
        "800000099",
    }


def test_monthly_trend_filters_by_nit(db):
    trend_a = db_service.get_monthly_trend(
        db, account_prefix="4", months=12, company_nit=NIT_A
    )
    total_credit_a = sum(row["total_credit"] for row in trend_a)
    assert total_credit_a == 1500.0

    trend_b = db_service.get_monthly_trend(
        db, account_prefix="4", months=12, company_nit=NIT_B
    )
    total_credit_b = sum(row["total_credit"] for row in trend_b)
    assert total_credit_b == 7777.0


def test_monthly_totals_by_class_filters_by_nit(db):
    totals_a = db_service.get_monthly_totals_by_class(db, months=12, company_nit=NIT_A)
    assert sum(r["total_credit"] for r in totals_a["ingresos"]) == 1500.0

    totals_b = db_service.get_monthly_totals_by_class(db, months=12, company_nit=NIT_B)
    assert sum(r["total_credit"] for r in totals_b["ingresos"]) == 7777.0
