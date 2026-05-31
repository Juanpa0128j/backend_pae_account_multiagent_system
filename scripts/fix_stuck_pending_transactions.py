#!/usr/bin/env python3
"""
Bulk-retry stuck pending transactions by creating ProcessJobs for them.

After the jobs.py fix (processing first PENDING tx + auto-chaining),
calling this script creates an initial ProcessJob for each ingest_id
that still has pending transactions. The chained jobs will then process
the rest automatically.

Run inside the devcontainer:
    uv run python scripts/fix_stuck_pending_transactions.py [--dry-run]

Or with the venv activated:
    python scripts/fix_stuck_pending_transactions.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text

# Add the project root to the path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.database import TransactionStatus
from app.services import db_service, jobs


def _find_stuck_ingest_ids(session):
    """Find ingest_ids that have pending transactions but no active ProcessJob."""
    rows = session.execute(text("""
            SELECT DISTINCT tp.ingest_id, COUNT(tp.id) as pending_count
            FROM transactions_pending tp
            WHERE tp.status = 'PENDING'
            AND NOT EXISTS (
                SELECT 1 FROM process_jobs pj
                WHERE pj.ingest_id = tp.ingest_id
                AND pj.status IN ('QUEUED', 'RUNNING', 'PENDING_AUDIT_REVIEW')
            )
            GROUP BY tp.ingest_id
            ORDER BY pending_count DESC
        """)).mappings().all()
    return rows


async def _create_and_start_job(ingest_id: str, pending_count: int, dry_run: bool):
    """Create a ProcessJob for the ingest_id and start it."""
    if dry_run:
        print(
            f"  [DRY-RUN] Would create ProcessJob for {ingest_id} ({pending_count} pending)"
        )
        return True

    db = SessionLocal()
    try:
        # Validate ingest job exists and has staged transactions
        ingest_job = db_service.get_ingest_job(db, ingest_id)
        if not ingest_job:
            print(f"  [SKIP] IngestJob {ingest_id} not found")
            return False

        staged = db_service.get_transactions_by_ingest(db, ingest_id)
        pending_staged = [tx for tx in staged if tx.status == TransactionStatus.PENDING]
        if not pending_staged:
            print(f"  [SKIP] No pending transactions for {ingest_id}")
            return False

        # Validate company settings exist (same check as the API endpoint)
        company_nit = ingest_job.company_nit
        if not company_nit:
            print(f"  [SKIP] IngestJob {ingest_id} has no company_nit")
            return False

        company_settings = db_service.get_company_settings(db, company_nit)
        if not company_settings:
            print(
                f"  [SKIP] No company settings for NIT {company_nit}. "
                f"Configure empresa first in /settings."
            )
            return False

        # Create ProcessJob
        process_job = db_service.create_process_job(
            db, ingest_id, created_by="bulk_retry_script"
        )
        print(
            f"  [CREATED] ProcessJob {process_job.id} for {ingest_id} "
            f"({len(pending_staged)} pending transactions)"
        )

        # Start the job (this will auto-chain if more pending txs remain)
        await jobs.start_process_job(process_job.id)
        print(f"  [STARTED] ProcessJob {process_job.id}")
        return True

    except Exception as exc:
        print(f"  [ERROR] Failed for {ingest_id}: {exc}")
        return False
    finally:
        db.close()


async def main(dry_run: bool = False):
    print("=" * 70)
    print("PAE Bulk Retry — Stuck Pending Transactions")
    print("=" * 70)
    print()

    db = SessionLocal()
    try:
        stuck = _find_stuck_ingest_ids(db)
    finally:
        db.close()

    if not stuck:
        print(
            "No stuck ingest_ids found. All pending transactions have active ProcessJobs."
        )
        return

    total_pending = sum(r["pending_count"] for r in stuck)
    print(
        f"Found {len(stuck)} ingest_id(s) with {total_pending} total pending transactions:"
    )
    for row in stuck:
        print(f"  {row['ingest_id']}: {row['pending_count']} pending")
    print()

    if dry_run:
        print("Running in DRY-RUN mode. No jobs will be created.")
        print()

    created = 0
    skipped = 0
    for row in stuck:
        ingest_id = row["ingest_id"]
        pending_count = row["pending_count"]
        print(f"Processing {ingest_id}...")
        ok = await _create_and_start_job(ingest_id, pending_count, dry_run)
        if ok:
            created += 1
        else:
            skipped += 1

    print()
    print("=" * 70)
    print("Summary:")
    print(f"  Ingest IDs processed: {len(stuck)}")
    print(f"  ProcessJobs created:  {created}")
    print(f"  Skipped:              {skipped}")
    print(f"  Total pending txs:    {total_pending}")
    if not dry_run and created > 0:
        print()
        print(
            "Jobs are running in the background. Each job auto-chains to the next "
            "pending transaction for the same ingest_id."
        )
        print("Monitor progress via:")
        print("  - Dashboard (/api/v1/dashboard/stats)")
        print("  - Process status endpoint (/api/v1/process/status/{process_id})")
        print("  - Transactions page (refresh to see posted transactions appear)")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk-create ProcessJobs for stuck pending transactions"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without creating any jobs",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
