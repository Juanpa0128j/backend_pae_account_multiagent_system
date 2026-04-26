"""Verify /api/v1/dashboard/* endpoints scope counters by company_nit."""

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
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from main import app


NIT_A = "900111111"
NIT_B = "900222222"


@pytest.fixture()
def client():
    """TestClient with in-memory SQLite + 2 NITs of seed data."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    now = datetime.now(timezone.utc).replace(microsecond=0)

    # IngestJobs (parents for FK)
    for ingest_id in ("ing_A", "ing_B"):
        session.add(
            IngestJob(
                id=ingest_id,
                file_name=f"{ingest_id}.pdf",
                status=IngestStatus.COMPLETED,
            )
        )
    session.flush()

    # NIT_A — 2 PENDING + 1 REJECTED
    pending_rows = [
        TransactionPending(
            id=f"txn_A_{i}",
            ingest_id="ing_A",
            fecha=now,
            company_nit=NIT_A,
            total=Decimal("100"),
            status=TransactionStatus.PENDING,
        )
        for i in range(2)
    ]
    pending_rows.append(
        TransactionPending(
            id="txn_A_rej",
            ingest_id="ing_A",
            fecha=now,
            company_nit=NIT_A,
            total=Decimal("50"),
            status=TransactionStatus.REJECTED,
        )
    )

    # NIT_B — 1 PENDING + 2 REJECTED
    pending_rows.append(
        TransactionPending(
            id="txn_B_pend",
            ingest_id="ing_B",
            fecha=now,
            company_nit=NIT_B,
            total=Decimal("200"),
            status=TransactionStatus.PENDING,
        )
    )
    pending_rows.extend(
        [
            TransactionPending(
                id=f"txn_B_rej_{i}",
                ingest_id="ing_B",
                fecha=now,
                company_nit=NIT_B,
                total=Decimal("75"),
                status=TransactionStatus.REJECTED,
            )
            for i in range(2)
        ]
    )

    session.add_all(pending_rows)
    session.flush()

    # TransactionPosted — NIT_A has 1, NIT_B has 3
    posted_rows = [
        TransactionPosted(
            id="posted_A_1",
            transaction_pending_id="txn_A_0",
            company_nit=NIT_A,
            cuenta_puc="519595",
            status=TransactionStatus.POSTED,
        ),
    ]
    for i in range(3):
        posted_rows.append(
            TransactionPosted(
                id=f"posted_B_{i}",
                transaction_pending_id="txn_B_pend" if i == 0 else f"txn_B_rej_{i - 1}",
                company_nit=NIT_B,
                cuenta_puc="519595",
                status=TransactionStatus.POSTED,
            )
        )
    session.add_all(posted_rows)
    session.commit()

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    session.close()


def test_dashboard_stats_filters_by_nit(client):
    rsp_a = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_A})
    assert rsp_a.status_code == 200
    data_a = rsp_a.json()
    assert data_a["documentos_pendientes"] == 2
    assert data_a["transacciones_procesadas_mes"] == 1
    assert data_a["alertas_activas"] == 1

    rsp_b = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_B})
    assert rsp_b.status_code == 200
    data_b = rsp_b.json()
    assert data_b["documentos_pendientes"] == 1
    assert data_b["transacciones_procesadas_mes"] == 3
    assert data_b["alertas_activas"] == 2


def test_dashboard_stats_without_nit_returns_all(client):
    rsp = client.get("/api/v1/dashboard/stats")
    assert rsp.status_code == 200
    data = rsp.json()
    assert data["documentos_pendientes"] == 3
    assert data["transacciones_procesadas_mes"] == 4
    assert data["alertas_activas"] == 3


def test_dashboard_stats_txn_counts_filtered(client):
    rsp_a = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_A})
    counts_a = rsp_a.json()["transacciones_por_estado"]
    assert counts_a.get("pending", 0) == 2
    assert counts_a.get("rejected", 0) == 1

    rsp_b = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_B})
    counts_b = rsp_b.json()["transacciones_por_estado"]
    assert counts_b.get("pending", 0) == 1
    assert counts_b.get("rejected", 0) == 2
