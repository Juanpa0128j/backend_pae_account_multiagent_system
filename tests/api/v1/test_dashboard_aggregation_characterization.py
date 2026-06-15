"""Characterization tests for dashboard aggregation endpoints (perf fix B2).

Seeds realistic JournalEntryLine data across PUC classes, IVA sub-accounts,
retention accounts, multiple months, and two companies, then asserts the EXACT
numeric output of /stats, /financial-summary, and /monthly-trend.

These lock in the financial numbers BEFORE the loop->SQL aggregation refactor so
the SQL-vs-Python equivalence is provable. Numbers must stay byte-identical
after the refactor.
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
    AuditLog,
    IngestJob,
    IngestStatus,
    JournalEntryLine,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)
from main import app

NIT_A = "900111111"
NIT_B = "900222222"

# Use a fixed recent month so the rolling `months` window in monthly-trend
# (anchored on datetime.now) always includes the seeded data. The current month
# is computed at runtime so the test does not rot over time.
_NOW = datetime.now(timezone.utc)


def _ym(months_back: int) -> datetime:
    """Return a UTC datetime on the 15th of the month `months_back` before now."""
    year = _NOW.year
    month = _NOW.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 15, 12, 0, 0, tzinfo=timezone.utc)


def _line(posted_id, nit, code, debito, credito, fecha):
    return JournalEntryLine(
        transaction_posted_id=posted_id,
        company_nit=nit,
        cuenta_puc=code,
        debito=Decimal(str(debito)),
        credito=Decimal(str(credito)),
        fecha=fecha,
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

    # Pending rows (for txn counters) — A: 2 pending, 1 rejected; B: 1 pending.
    pend = [
        TransactionPending(
            id="p_a0",
            ingest_id="ing",
            company_nit=NIT_A,
            total=Decimal("1"),
            status=TransactionStatus.PENDING,
        ),
        TransactionPending(
            id="p_a1",
            ingest_id="ing",
            company_nit=NIT_A,
            total=Decimal("1"),
            status=TransactionStatus.PENDING,
        ),
        TransactionPending(
            id="p_a2",
            ingest_id="ing",
            company_nit=NIT_A,
            total=Decimal("1"),
            status=TransactionStatus.REJECTED,
        ),
        TransactionPending(
            id="p_b0",
            ingest_id="ing",
            company_nit=NIT_B,
            total=Decimal("1"),
            status=TransactionStatus.PENDING,
        ),
    ]
    session.add_all(pend)
    session.flush()

    # Posted parents — one per company is enough; journal lines hang off these.
    session.add_all(
        [
            TransactionPosted(
                id="tp_a",
                transaction_pending_id="p_a0",
                company_nit=NIT_A,
                cuenta_puc="0000",
                status=TransactionStatus.POSTED,
            ),
            TransactionPosted(
                id="tp_b",
                transaction_pending_id="p_b0",
                company_nit=NIT_B,
                cuenta_puc="0000",
                status=TransactionStatus.POSTED,
            ),
            # A POSTED parent that is NOT status POSTED -> its lines must be
            # excluded by every aggregation (status filter characterization).
            TransactionPosted(
                id="tp_a_rej",
                transaction_pending_id="p_a1",
                company_nit=NIT_A,
                cuenta_puc="0000",
                status=TransactionStatus.REJECTED,
            ),
        ]
    )
    session.flush()

    m0, m1, m2 = _ym(0), _ym(1), _ym(2)

    lines = [
        # ---- Company A ----
        # Cash class 11 (efectivo = debit - credit net)
        _line("tp_a", NIT_A, "1105", 1000.50, 200.00, m0),
        _line("tp_a", NIT_A, "1110", 500.00, 0.00, m1),
        # Other asset class 1 (assets)
        _line("tp_a", NIT_A, "1305", 800.00, 0.00, m0),
        # Liability class 2 (generic)
        _line("tp_a", NIT_A, "2205", 0.00, 1500.00, m0),
        # IVA generado 240805 (credit side)
        _line("tp_a", NIT_A, "240805", 0.00, 380.00, m0),
        _line("tp_a", NIT_A, "240805", 20.00, 0.00, m1),
        # IVA descontable 240802 (debit side)
        _line("tp_a", NIT_A, "240802", 150.00, 0.00, m0),
        # IVA descontable 240810 (debit side)
        _line("tp_a", NIT_A, "240810", 30.00, 0.00, m1),
        # IVA parent exact "2408" — net debit > 0 -> descontable
        _line("tp_a", NIT_A, "2408", 90.00, 40.00, m0),
        # Retefuente por pagar 2365 (credit - debit)
        _line("tp_a", NIT_A, "2365", 10.00, 260.00, m0),
        # ReteICA por pagar 2368 (credit - debit)
        _line("tp_a", NIT_A, "2368", 5.00, 95.00, m1),
        # Equity class 3
        _line("tp_a", NIT_A, "3115", 0.00, 700.00, m0),
        # Revenue class 4 (credit - debit) across 2 months
        _line("tp_a", NIT_A, "4135", 0.00, 2000.00, m0),
        _line("tp_a", NIT_A, "4135", 100.00, 0.00, m1),
        _line("tp_a", NIT_A, "4175", 0.00, 333.33, m2),
        # Expenses class 5 (debit - credit) across 2 months
        _line("tp_a", NIT_A, "5135", 600.00, 0.00, m0),
        _line("tp_a", NIT_A, "5205", 250.00, 50.00, m1),
        # Cost of sales class 6
        _line("tp_a", NIT_A, "6135", 400.00, 0.00, m0),
        # Lines that must be EXCLUDED (parent not POSTED)
        _line("tp_a_rej", NIT_A, "4135", 0.00, 99999.00, m0),
        _line("tp_a_rej", NIT_A, "1105", 99999.00, 0.00, m0),
        # ---- Company B (must not leak into A) ----
        _line("tp_b", NIT_B, "1105", 7777.00, 0.00, m0),
        _line("tp_b", NIT_B, "4135", 0.00, 5555.00, m0),
        _line("tp_b", NIT_B, "5135", 1111.00, 0.00, m1),
        _line("tp_b", NIT_B, "240805", 0.00, 222.00, m0),
    ]
    session.add_all(lines)

    # Recent activity (audit logs) — A gets 1, plus a NULL-nit row.
    session.add_all(
        [
            AuditLog(action="a1", entity_type="t", entity_id="x", company_nit=NIT_A),
            AuditLog(action="a2", entity_type="t", entity_id="y", company_nit=None),
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


# ── Expected values for Company A, computed by hand using current logic ──────
#
# efectivo (class 11, debit-credit):
#   1105: 1000.50 - 200 = 800.50 ; 1110: 500 - 0 = 500 -> 1300.50
# assets (class 1, balance sheet debit-credit, no naturaleza override seeded):
#   1105 net 800.50 + 1110 net 500 + 1305 net 800 = 2100.50
# liabilities (class 2, credit-debit):
#   2205: 1500 ; 240805: 380-20=360 ; 240802: -150 ; 240810: -30 ;
#   2408: 40-90=-50 ; 2365: 260-10=250 ; 2368: 95-5=90
#   = 1500+360-150-30-50+250+90 = 1970.00
# equity (class 3, credit-debit): 3115 = 700
# revenue (class 4, credit-debit): 2000 - 100 + 333.33 = 2233.33
# expenses (class 5, debit-credit): 600 + (250-50)=200 -> 800
# cost_of_sales (class 6, debit-credit): 400
# net_profit = revenue - expenses - cost = 2233.33 - 800 - 400 = 1033.33
#
# iva (stats & summary):
#   iva_generado = 240805 credit 380 (only credit counted) = 380
#   iva_descontable = 240802 debit 150 + 240810 debit 30 + 2408 net(90-40=50) = 230
#   iva_por_pagar = 380 - 230 = 150
# retenciones:
#   retfte 2365 = credit-debit = 260-10 = 250
#   retica 2368 = credit-debit = 95-5 = 90
#   total = 340
EXP_A = {
    "total_activos": 2100.50,
    "total_pasivos": 1970.00,
    "patrimonio": 700.00,
    "utilidad_neta": 1033.33,
    "efectivo": 1300.50,
    "iva_por_pagar": 150.00,
    "total_retenciones": 340.00,
    "ingresos": 2233.33,
    "gastos": 800.00,
}


def test_stats_numbers_company_a(client):
    r = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_A})
    assert r.status_code == 200
    d = r.json()
    assert d["total_activos_cop"] == EXP_A["total_activos"]
    assert d["total_pasivos_cop"] == EXP_A["total_pasivos"]
    assert d["utilidad_neta_cop"] == EXP_A["utilidad_neta"]
    assert d["efectivo_disponible_cop"] == EXP_A["efectivo"]
    assert d["iva_por_pagar"] == EXP_A["iva_por_pagar"]
    assert d["total_retenciones"] == EXP_A["total_retenciones"]
    # counters
    assert d["documentos_pendientes"] == 2
    assert d["alertas_activas"] == 1
    assert d["transacciones_por_estado"].get("pending") == 2
    assert d["transacciones_por_estado"].get("rejected") == 1


def test_financial_summary_numbers_company_a(client):
    r = client.get("/api/v1/dashboard/financial-summary", params={"company_nit": NIT_A})
    assert r.status_code == 200
    d = r.json()
    assert d["total_activos"] == EXP_A["total_activos"]
    assert d["total_pasivos"] == EXP_A["total_pasivos"]
    assert d["patrimonio"] == EXP_A["patrimonio"]
    assert d["utilidad_neta"] == EXP_A["utilidad_neta"]
    assert d["efectivo_disponible"] == EXP_A["efectivo"]
    assert d["iva_por_pagar"] == EXP_A["iva_por_pagar"]
    assert d["total_retenciones"] == EXP_A["total_retenciones"]
    assert d["ingresos_periodo"] == EXP_A["ingresos"]
    assert d["gastos_periodo"] == EXP_A["gastos"]
    assert len(d["actividad_reciente"]) == 1  # NULL-nit row excluded


def test_monthly_trend_numbers_company_a(client):
    r = client.get(
        "/api/v1/dashboard/monthly-trend",
        params={"company_nit": NIT_A, "months": 24},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    # Build {month_label: (ingresos, gastos)} for assertion independent of order.
    by_label = {p["month"]: (p["ingresos"], p["gastos"]) for p in data}

    from app.api.v1.dashboard import _label_ym

    m0, m1, m2 = _ym(0), _ym(1), _ym(2)
    lbl0 = _label_ym(f"{m0.year}-{m0.month:02d}")
    lbl1 = _label_ym(f"{m1.year}-{m1.month:02d}")
    lbl2 = _label_ym(f"{m2.year}-{m2.month:02d}")

    # ingresos = class4 credit-debit per month ; gastos = class5 debit-credit
    # m0: ing 4135 credit 2000 = 2000 ; gas 5135 debit 600 = 600
    # m1: ing 4135 debit 100 -> -100 ; gas 5205 250-50 = 200
    # m2: ing 4175 credit 333.33 = 333.33 ; gas 0
    assert by_label[lbl0] == (2000.0, 600.0)
    assert by_label[lbl1] == (-100.0, 200.0)
    assert by_label[lbl2] == (333.33, 0.0)


def test_company_b_isolated(client):
    r = client.get("/api/v1/dashboard/stats", params={"company_nit": NIT_B})
    d = r.json()
    # B efectivo = 1105 debit 7777 ; assets same ; revenue 5555 ; iva gen 222
    assert d["efectivo_disponible_cop"] == 7777.00
    assert d["total_activos_cop"] == 7777.00
    assert d["iva_por_pagar"] == 222.00  # only 240805 credit, no descontable
    # net_profit = revenue 5555 - expenses 1111 (5135) = 4444 (all-time)
    assert d["utilidad_neta_cop"] == 4444.00


def test_global_no_nit_aggregates_both(client):
    """No NIT -> A and B combined (rejected-parent lines still excluded)."""
    r = client.get("/api/v1/dashboard/stats")
    d = r.json()
    # efectivo = A 1300.50 + B 7777 = 9077.50
    assert d["efectivo_disponible_cop"] == 9077.50
    # iva_por_pagar = generado(380+222) - descontable(230) = 372
    assert d["iva_por_pagar"] == 372.00
