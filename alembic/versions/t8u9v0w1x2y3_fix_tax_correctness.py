"""fix_tax_correctness — F2/F3/F4 tax fixes

Fix 2: Partial unique index on transactions_posted natural key
  (company_nit, nit_emisor via pending join) — implemented via
  composite index on transactions_posted(company_nit) + transactions_pending join;
  the guard lives in application code (persist_node.py find_duplicate_posted).
  This migration adds a DB-level partial unique index on the natural key columns
  available on transactions_posted itself: (company_nit, transaction_pending_id).
  The full natural-key guard (nit_emisor, fecha::date, total) is enforced in
  app code via find_duplicate_posted().

Fix 3: Add base_minima_uvt column to reteica_tarifas for municipal ReteICA base
  (Fix 4 of the spec — column needed for municipal override).

Idempotent: uses information_schema checks before any DDL.

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-05-24 17:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "t8u9v0w1x2y3"
down_revision: Union[str, None] = "s7t8u9v0w1x2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Fix 2: unique index on transactions_posted(company_nit, transaction_pending_id)
    # transaction_pending_id is already a FK but not UNIQUE — enforce 1:1 at DB level.
    idx_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE tablename = 'transactions_posted' "
            "AND indexname = 'uq_transactions_posted_pending_id'"
        )
    ).fetchone()
    if not idx_exists:
        op.create_index(
            "uq_transactions_posted_pending_id",
            "transactions_posted",
            ["transaction_pending_id"],
            unique=True,
        )

    # ── Fix 4: add base_minima_uvt to reteica_tarifas (municipal ReteICA base)
    col_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'reteica_tarifas' "
            "AND column_name = 'base_minima_uvt'"
        )
    ).fetchone()
    if not col_exists:
        op.add_column(
            "reteica_tarifas",
            sa.Column(
                "base_minima_uvt",
                sa.Numeric(8, 2),
                server_default="4",
                nullable=True,
                comment=(
                    "Municipal ReteICA base mínima in UVT units. "
                    "Bogotá=4, Medellín=15, Cali=3. Default 4 UVT (Bogotá reference). "
                    "Decreto 572 does NOT apply to ReteICA — each municipio sets own base."
                ),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()

    idx_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes "
            "WHERE tablename = 'transactions_posted' "
            "AND indexname = 'uq_transactions_posted_pending_id'"
        )
    ).fetchone()
    if idx_exists:
        op.drop_index(
            "uq_transactions_posted_pending_id", table_name="transactions_posted"
        )

    col_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'reteica_tarifas' "
            "AND column_name = 'base_minima_uvt'"
        )
    ).fetchone()
    if col_exists:
        op.drop_column("reteica_tarifas", "base_minima_uvt")
