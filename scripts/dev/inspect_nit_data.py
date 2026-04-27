"""Inspect NIT distribution in journal_entry_lines and optionally seed test data.

Use cases:
    # 1) Diagnose: see which NITs exist and what IVA/income totals they have.
    uv run python scripts/dev/inspect_nit_data.py

    # 2) Seed two synthetic NITs with distinct IVA totals for end-to-end testing.
    uv run python scripts/dev/inspect_nit_data.py --seed

    # 3) Remove the synthetic rows when done.
    uv run python scripts/dev/inspect_nit_data.py --cleanup

The seed creates rows tagged with comprobante='NIT_SEED' so cleanup is safe.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlalchemy import func, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.database import (  # noqa: E402
    CompanySettings,
    IngestJob,
    IngestStatus,
    JournalEntryLine,
    TransactionPending,
    TransactionPosted,
    TransactionStatus,
)


SEED_MARKER = "NIT_SEED"
SEED_NITS = ("900111111", "900222222")


# IVA accounts per project convention
IVA_GENERADO = "240808"
IVA_DESCONTABLE = "240802"


def report(db) -> None:
    print("\n=== NIT distribution in journal_entry_lines ===\n")

    rows = db.execute(
        select(
            JournalEntryLine.company_nit,
            func.count(JournalEntryLine.id).label("n_lines"),
            func.sum(JournalEntryLine.debito).label("total_debito"),
            func.sum(JournalEntryLine.credito).label("total_credito"),
        ).group_by(JournalEntryLine.company_nit)
    ).all()

    if not rows:
        print("  (no journal_entry_lines rows)")
        return

    print(f"{'company_nit':<22}{'lines':>8}{'debito':>16}{'credito':>16}")
    print("-" * 62)
    for r in rows:
        nit_label = r.company_nit if r.company_nit else "<NULL>"
        print(
            f"{nit_label:<22}{r.n_lines:>8}"
            f"{float(r.total_debito or 0):>16,.0f}"
            f"{float(r.total_credito or 0):>16,.0f}"
        )

    print("\n=== IVA totals per NIT ===\n")
    iva_rows = db.execute(
        select(
            JournalEntryLine.company_nit,
            JournalEntryLine.cuenta_puc,
            func.sum(JournalEntryLine.debito).label("debito"),
            func.sum(JournalEntryLine.credito).label("credito"),
        )
        .where(JournalEntryLine.cuenta_puc.in_([IVA_GENERADO, IVA_DESCONTABLE]))
        .group_by(JournalEntryLine.company_nit, JournalEntryLine.cuenta_puc)
    ).all()

    if not iva_rows:
        print("  (no IVA accounts found — seed data first)")
        return

    print(f"{'NIT':<22}{'cuenta':<12}{'debito':>16}{'credito':>16}")
    print("-" * 66)
    for r in iva_rows:
        nit_label = r.company_nit if r.company_nit else "<NULL>"
        print(
            f"{nit_label:<22}{r.cuenta_puc:<12}"
            f"{float(r.debito or 0):>16,.0f}"
            f"{float(r.credito or 0):>16,.0f}"
        )


def _ensure_parent(db, ingest_id: str, txn_id: str, posted_id: str, nit: str) -> None:
    """Create the FK chain (CompanySettings + IngestJob -> Pending -> Posted) if missing."""
    if not db.get(CompanySettings, nit):
        db.add(
            CompanySettings(
                nit=nit,
                nombre=f"Empresa Demo {nit}",
                ciudad="Bogota",
                codigo_ciiu="6920",
                iva_responsable=True,
                es_declarante=True,
            )
        )
        db.flush()
    if not db.get(IngestJob, ingest_id):
        db.add(
            IngestJob(
                id=ingest_id,
                file_name=f"{ingest_id}.seed.pdf",
                status=IngestStatus.COMPLETED,
            )
        )
        db.flush()
    if not db.get(TransactionPending, txn_id):
        db.add(
            TransactionPending(
                id=txn_id,
                ingest_id=ingest_id,
                fecha=datetime.now(timezone.utc),
                company_nit=nit,
                total=Decimal("1000"),
                status=TransactionStatus.PENDING,
            )
        )
        db.flush()
    if not db.get(TransactionPosted, posted_id):
        db.add(
            TransactionPosted(
                id=posted_id,
                transaction_pending_id=txn_id,
                company_nit=nit,
                cuenta_puc="519595",
                status=TransactionStatus.POSTED,
            )
        )
        db.flush()


def seed(db) -> None:
    print("\n=== Seeding synthetic NIT data (comprobante=%s) ===\n" % SEED_MARKER)
    now = datetime.now(timezone.utc)

    # NIT A: ingresos 5,000,000  +  IVA generado 950,000
    # NIT B: ingresos 9,000,000  +  IVA generado 1,710,000  (distinct from A)
    plans = [
        {
            "nit": SEED_NITS[0],
            "ingest_id": "ing_seed_A",
            "txn_id": "txn_seed_A",
            "posted_id": "posted_seed_A",
            "ingresos": Decimal("5000000"),
            "iva_gen": Decimal("950000"),
        },
        {
            "nit": SEED_NITS[1],
            "ingest_id": "ing_seed_B",
            "txn_id": "txn_seed_B",
            "posted_id": "posted_seed_B",
            "ingresos": Decimal("9000000"),
            "iva_gen": Decimal("1710000"),
        },
    ]

    for plan in plans:
        _ensure_parent(
            db,
            plan["ingest_id"],
            plan["txn_id"],
            plan["posted_id"],
            plan["nit"],
        )

        rows = [
            JournalEntryLine(
                transaction_posted_id=plan["posted_id"],
                fecha=now,
                company_nit=plan["nit"],
                comprobante=SEED_MARKER,
                cuenta_puc="413595",
                cuenta_nombre="Ingresos servicios",
                tercero_nit="800000001",
                descripcion="Seed ingresos",
                debito=Decimal("0"),
                credito=plan["ingresos"],
            ),
            JournalEntryLine(
                transaction_posted_id=plan["posted_id"],
                fecha=now,
                company_nit=plan["nit"],
                comprobante=SEED_MARKER,
                cuenta_puc=IVA_GENERADO,
                cuenta_nombre="IVA generado",
                tercero_nit="800000001",
                descripcion="Seed IVA",
                debito=Decimal("0"),
                credito=plan["iva_gen"],
            ),
            JournalEntryLine(
                transaction_posted_id=plan["posted_id"],
                fecha=now,
                company_nit=plan["nit"],
                comprobante=SEED_MARKER,
                cuenta_puc="111005",
                cuenta_nombre="Bancos",
                tercero_nit="800000001",
                descripcion="Seed cash",
                debito=plan["ingresos"] + plan["iva_gen"],
                credito=Decimal("0"),
            ),
        ]
        db.add_all(rows)

    db.commit()
    print(f"  Seeded NIT_A={SEED_NITS[0]} and NIT_B={SEED_NITS[1]}")
    print("  Test queries:")
    print(f"    GET /api/v1/reports/iva?company_nit={SEED_NITS[0]}")
    print(f"    GET /api/v1/reports/iva?company_nit={SEED_NITS[1]}")
    print(
        "    Or via chat: send a message asking 'reporte de IVA' with company_nit set."
    )


def cleanup(db) -> None:
    print("\n=== Removing synthetic NIT seed rows ===\n")

    deleted_lines = (
        db.query(JournalEntryLine)
        .filter(JournalEntryLine.comprobante == SEED_MARKER)
        .delete(synchronize_session=False)
    )

    deleted_posted = (
        db.query(TransactionPosted)
        .filter(TransactionPosted.id.in_(("posted_seed_A", "posted_seed_B")))
        .delete(synchronize_session=False)
    )

    deleted_pending = (
        db.query(TransactionPending)
        .filter(TransactionPending.id.in_(("txn_seed_A", "txn_seed_B")))
        .delete(synchronize_session=False)
    )

    deleted_ingest = (
        db.query(IngestJob)
        .filter(IngestJob.id.in_(("ing_seed_A", "ing_seed_B")))
        .delete(synchronize_session=False)
    )

    deleted_settings = (
        db.query(CompanySettings)
        .filter(CompanySettings.nit.in_(SEED_NITS))
        .delete(synchronize_session=False)
    )

    db.commit()
    print(f"  Removed {deleted_lines} journal_entry_lines")
    print(f"  Removed {deleted_posted} transactions_posted")
    print(f"  Removed {deleted_settings} company_settings")
    print(f"  Removed {deleted_pending} transactions_pending")
    print(f"  Removed {deleted_ingest} ingest_jobs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed", action="store_true", help="Insert two synthetic NITs with IVA data."
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete all rows tagged with comprobante='NIT_SEED'.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.cleanup:
            cleanup(db)
        if args.seed:
            seed(db)
        report(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
