"""add company_nit to audit_logs

Revision ID: c7d8e9f0a1b2
Revises: 85397898945d
Create Date: 2026-04-26 02:00:00.000000

Adds a tenant column to audit_logs and backfills existing rows by joining
through transactions_pending, transactions_posted, and (transitively)
ingest_jobs via the first known transaction.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "85397898945d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _index_exists(name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
        {"n": name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("audit_logs", "company_nit"):
        op.add_column(
            "audit_logs",
            sa.Column("company_nit", sa.String(20), nullable=True),
        )
    if not _index_exists("ix_audit_logs_company_nit"):
        op.create_index(
            "ix_audit_logs_company_nit",
            "audit_logs",
            ["company_nit"],
        )

    # Backfill 1: entity_type='transaction' against transactions_pending
    op.execute(
        """
        UPDATE audit_logs AS al
        SET company_nit = tp.company_nit
        FROM transactions_pending AS tp
        WHERE al.entity_type = 'transaction'
          AND al.entity_id = tp.id
          AND al.company_nit IS NULL
          AND tp.company_nit IS NOT NULL
        """
    )

    # Backfill 2: entity_type='transaction' against transactions_posted
    # (same entity_type, but TransactionPosted ids use 'posted_' prefix)
    op.execute(
        """
        UPDATE audit_logs AS al
        SET company_nit = tposted.company_nit
        FROM transactions_posted AS tposted
        WHERE al.entity_type = 'transaction'
          AND al.entity_id = tposted.id
          AND al.company_nit IS NULL
          AND tposted.company_nit IS NOT NULL
        """
    )

    # Backfill 3: entity_type='ingest' via any transaction sharing the
    # ingest_id. IngestJob itself has no company_nit column.
    op.execute(
        """
        UPDATE audit_logs AS al
        SET company_nit = sub.company_nit
        FROM (
            SELECT DISTINCT ON (ingest_id)
                ingest_id,
                company_nit
            FROM transactions_pending
            WHERE company_nit IS NOT NULL
            ORDER BY ingest_id, created_at
        ) AS sub
        WHERE al.entity_type = 'ingest'
          AND al.entity_id = sub.ingest_id
          AND al.company_nit IS NULL
        """
    )

    # entity_type='process' rows are not backfilled — ProcessJob has no
    # company_nit column. They remain NULL and will be excluded from
    # tenant-scoped queries (WHERE company_nit = X).


def downgrade() -> None:
    if _index_exists("ix_audit_logs_company_nit"):
        op.drop_index("ix_audit_logs_company_nit", table_name="audit_logs")
    if _column_exists("audit_logs", "company_nit"):
        op.drop_column("audit_logs", "company_nit")
