"""Dashboard exposure of class-1 credit-balance reclassifications.

/stats and /financial-summary must surface `cuentas_reclasificadas` (frontend
contract) sourced from get_balance_sheet()["reclassified_accounts"] so the UI
can explain why a receivable shows up inside pasivos (anticipo de cliente).
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.database import (
    IngestJob,
    IngestStatus,
    JournalEntryLine,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from main import app

NIT_RECLASS = "900111111"
NIT_CLEAN = "900222222"

_FECHA = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def _line(posted_id, nit, code, debito, credito):
    return JournalEntryLine(
        transaction_posted_id=posted_id,
        company_nit=nit,
        cuenta_puc=code,
        debito=Decimal(str(debito)),
        credito=Decimal(str(credito)),
        fecha=_FECHA,
    )


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    session.add(IngestJob(id="ing", file_name="ing.pdf", status=IngestStatus.COMPLETED))
    session.flush()
    session.add_all(
        [
            TransactionPending(
                id="p_r",
                ingest_id="ing",
                company_nit=NIT_RECLASS,
                total=Decimal("1"),
                status=TransactionStatus.POSTED,
            ),
            TransactionPending(
                id="p_c",
                ingest_id="ing",
                company_nit=NIT_CLEAN,
                total=Decimal("1"),
                status=TransactionStatus.POSTED,
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            TransactionPosted(
                id="tp_r",
                transaction_pending_id="p_r",
                company_nit=NIT_RECLASS,
                cuenta_puc="0000",
                status=TransactionStatus.POSTED,
            ),
            TransactionPosted(
                id="tp_c",
                transaction_pending_id="p_c",
                company_nit=NIT_CLEAN,
                cuenta_puc="0000",
                status=TransactionStatus.POSTED,
            ),
        ]
    )
    session.flush()

    session.add_all(
        [
            # Reclass company: cobro posted before its factura — 130505 nets CREDIT.
            _line("tp_r", NIT_RECLASS, "111005", 500000, 0),
            _line("tp_r", NIT_RECLASS, "130505", 0, 500000),
            # Clean company: plain debit-nature asset, nothing to reclassify.
            _line("tp_c", NIT_CLEAN, "110505", 100000, 0),
            _line("tp_c", NIT_CLEAN, "310505", 0, 100000),
        ]
    )
    session.commit()

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    session.close()


def test_stats_exposes_cuentas_reclasificadas(client):
    r = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_RECLASS})
    assert r.status_code == 200
    assert r.json()["cuentas_reclasificadas"] == ["130505"]


def test_stats_empty_when_no_reclass(client):
    r = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_CLEAN})
    assert r.status_code == 200
    assert r.json()["cuentas_reclasificadas"] == []


def test_financial_summary_exposes_cuentas_reclasificadas(client):
    r = client.get(
        "/api/v1/dashboard/financial-summary", params={"company_nit": NIT_RECLASS}
    )
    assert r.status_code == 200
    assert r.json()["cuentas_reclasificadas"] == ["130505"]


def test_financial_summary_empty_when_no_reclass(client):
    r = client.get(
        "/api/v1/dashboard/financial-summary", params={"company_nit": NIT_CLEAN}
    )
    assert r.status_code == 200
    assert r.json()["cuentas_reclasificadas"] == []
