#!/usr/bin/env python3
"""
Diagnose stuck pending transactions in the PAE pipeline.

Run inside the devcontainer:
    uv run python scripts/diagnose_pending_transactions.py

Or with the venv activated:
    python scripts/diagnose_pending_transactions.py

Requires DATABASE_URL in the environment (already set in the devcontainer).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL is not set in the environment.")
    print("       Set it before running this script, e.g.:")
    print('       export DATABASE_URL="postgresql://..."')
    sys.exit(1)

engine = create_engine(DATABASE_URL, future=True)
Session = sessionmaker(bind=engine)

QUERIES = {
    "1. PENDING transactions per company": """
        SELECT company_nit, COUNT(*) as cnt
        FROM transactions_pending
        WHERE status = 'PENDING'
        GROUP BY company_nit
        ORDER BY cnt DESC
    """,
    "2. ProcessJobs for pending ingest_ids": """
        SELECT
            pj.id,
            pj.ingest_id,
            pj.status,
            LEFT(pj.error_message, 120) as error_snippet,
            pj.created_at,
            pj.current_agent,
            pj.current_stage
        FROM process_jobs pj
        WHERE pj.ingest_id IN (
            SELECT ingest_id
            FROM transactions_pending
            WHERE status = 'PENDING'
        )
        ORDER BY pj.created_at DESC
        LIMIT 50
    """,
    "3. IngestJob status for pending transactions": """
        SELECT
            ij.id,
            ij.status as ingest_status,
            ij.document_type,
            ij.file_name,
            ij.company_nit,
            ij.created_at
        FROM ingest_jobs ij
        WHERE ij.id IN (
            SELECT ingest_id
            FROM transactions_pending
            WHERE status = 'PENDING'
        )
        ORDER BY ij.created_at DESC
        LIMIT 50
    """,
    "4. ProcessJob status distribution": """
        SELECT status, COUNT(*) as cnt
        FROM process_jobs
        GROUP BY status
        ORDER BY cnt DESC
    """,
    "5. Old RUNNING/QUEUED jobs (possible deadlock)": """
        SELECT
            id,
            ingest_id,
            status,
            current_agent,
            current_stage,
            created_at,
            started_at
        FROM process_jobs
        WHERE status IN ('RUNNING', 'QUEUED')
          AND created_at < NOW() - INTERVAL '1 hour'
        ORDER BY created_at DESC
    """,
    "6. Overall pipeline health snapshot": """
        SELECT
            (SELECT COUNT(*) FROM transactions_pending WHERE status = 'PENDING') as pending_txn,
            (SELECT COUNT(*) FROM transactions_pending WHERE status = 'POSTED') as posted_txn,
            (SELECT COUNT(*) FROM transactions_posted) as total_posted,
            (SELECT COUNT(*) FROM process_jobs WHERE status = 'COMPLETED') as proc_completed,
            (SELECT COUNT(*) FROM process_jobs WHERE status = 'FAILED') as proc_failed,
            (SELECT COUNT(*) FROM process_jobs WHERE status = 'RUNNING') as proc_running,
            (SELECT COUNT(*) FROM process_jobs WHERE status = 'PENDING_AUDIT_REVIEW') as proc_audit_review,
            (SELECT COUNT(*) FROM process_jobs WHERE status = 'QUEUED') as proc_queued,
            (SELECT COUNT(*) FROM journal_entry_lines) as journal_lines
    """,
    "7. Failed ProcessJobs with error details": """
        SELECT
            id,
            ingest_id,
            status,
            error_message,
            created_at,
            current_agent,
            current_stage
        FROM process_jobs
        WHERE status = 'FAILED'
          AND ingest_id IN (
              SELECT ingest_id
              FROM transactions_pending
              WHERE status = 'PENDING'
          )
        ORDER BY created_at DESC
        LIMIT 20
    """,
    "8. Transactions pending but NO ProcessJob at all": """
        SELECT
            tp.id as pending_id,
            tp.ingest_id,
            tp.company_nit,
            tp.created_at,
            ij.status as ingest_status,
            ij.file_name
        FROM transactions_pending tp
        JOIN ingest_jobs ij ON tp.ingest_id = ij.id
        LEFT JOIN process_jobs pj ON ij.id = pj.ingest_id
        WHERE tp.status = 'PENDING'
          AND pj.id IS NULL
        ORDER BY tp.created_at DESC
        LIMIT 30
    """,
}


def _serialize(val):
    """Make SQLAlchemy row values JSON-serializable for display."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    return val


def run():
    session = Session()
    try:
        print("=" * 80)
        print("PAE Pipeline Diagnostic Report")
        print(f"Database: {(DATABASE_URL or '').split('@')[-1]}")
        print(f"Timestamp: {datetime.now(timezone.utc).isoformat()} UTC")
        print("=" * 80)
        print()

        for title, sql in QUERIES.items():
            print("-" * 80)
            print(title)
            print("-" * 80)
            result = session.execute(text(sql))
            rows = result.mappings().all()

            if not rows:
                print("  (no rows)")
                print()
                continue

            # Get headers from first row
            headers = list(rows[0].keys())
            col_widths = [len(h) for h in headers]

            # Calculate widths
            serialized_rows = []
            for row in rows:
                serialized = {k: _serialize(v) for k, v in dict(row).items()}
                serialized_rows.append(serialized)
                for i, h in enumerate(headers):
                    cell = str(serialized.get(h, ""))
                    col_widths[i] = max(col_widths[i], len(cell))

            # Print header
            header_line = " | ".join(
                h.ljust(col_widths[i]) for i, h in enumerate(headers)
            )
            print(f"  {header_line}")
            print(f"  {'-+-'.join('-' * w for w in col_widths)}")

            # Print rows
            for serialized in serialized_rows:
                line = " | ".join(
                    str(serialized.get(h, "")).ljust(col_widths[i])
                    for i, h in enumerate(headers)
                )
                print(f"  {line}")

            print()

        print("=" * 80)
        print("Interpretation guide:")
        print(
            "  • Query #8 = transactions that were ingested but NEVER had a ProcessJob."
        )
        print("    → Frontend likely failed to trigger processAccounting().")
        print("  • Query #7 = ProcessJobs that FAILED.")
        print("    → Read error_message, fix root cause, then retry.")
        print("  • Query #5 = Jobs stuck RUNNING for >1 hour.")
        print("    → Mark as FAILED and retry (possible deadlock / crash).")
        print("  • Query #4 = If RUNNING count is near 20, semaphore is saturated.")
        print("  • Query #2 status = PENDING_AUDIT_REVIEW → needs HITL confirmation.")
        print("=" * 80)

    finally:
        session.close()


if __name__ == "__main__":
    run()
