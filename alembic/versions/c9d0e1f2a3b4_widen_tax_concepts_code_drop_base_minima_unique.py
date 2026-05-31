"""Widen tax_concepts.code 16→32 + drop tax_base_minima (concepto, year) unique

The original ``tax_concepts.code`` column was ``VARCHAR(16)`` but the seeded
codes include ``servicios_pn_no_decl`` (20 chars) and ``servicios_pn_decl``
(17 chars), which fail to insert with ``StringDataRightTruncation``. Widen to
``VARCHAR(32)`` to match the canonical seed.

The ``tax_base_minima (concepto, year)`` UNIQUE constraint conflicted with the
temporal-window dataset that legitimately stores multiple rows for the same
(concepto, year) with different ``effective_from`` windows (e.g. Decreto 572
2025-06-01 → 2026-05-07 vs post-suspension 2026-05-08 → NULL). The seed
script's manual upsert already filters on the full composite key
(concepto, year, effective_from); the DB-level constraint was too narrow.

Idempotent: each ALTER / DROP guarded by information_schema / pg_constraint.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Widen tax_concepts.code 16 -> 32 (only if narrower).
    row = conn.execute(
        text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name='tax_concepts' AND column_name='code'"
        )
    ).first()
    if row is not None and row[0] is not None and row[0] < 32:
        op.alter_column(
            "tax_concepts",
            "code",
            existing_type=sa.String(row[0]),
            type_=sa.String(32),
            existing_nullable=False,
        )

    # Drop tax_base_minima (concepto, year) UNIQUE if present.
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname='tax_base_minima_concepto_year_key'"
        )
    ).first()
    if row is not None:
        op.drop_constraint(
            "tax_base_minima_concepto_year_key",
            "tax_base_minima",
            type_="unique",
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Re-create (concepto, year) UNIQUE only if currently absent
    # (safe only if no duplicate pairs).
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname='tax_base_minima_concepto_year_key'"
        )
    ).first()
    if row is None:
        op.create_unique_constraint(
            "tax_base_minima_concepto_year_key",
            "tax_base_minima",
            ["concepto", "year"],
        )

    # Narrow tax_concepts.code 32 -> 16 (only safe if no codes >16 chars).
    op.alter_column(
        "tax_concepts",
        "code",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
