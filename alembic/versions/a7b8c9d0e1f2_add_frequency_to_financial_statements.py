"""add frequency column to financial_statements

Distinguishes monthly / quarterly / annual / custom statements. Required for
the Vía B derivation rework: NIC 7 indirect cash flow + cambios de patrimonio
+ notas only make sense from annual closings, not from monthly snapshots.

The column is nullable so legacy rows (uploaded before this migration) keep
working — readers fall back to ``infer_frequency(period_start, period_end)``
when the column is NULL. We also backfill existing rows from the span between
``period_start`` and ``period_end`` so dashboards don't show ``Unknown`` for
data that's already there.

Idempotent: only adds the column when it doesn't already exist.

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-05-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "z6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMN_NAME = "frequency"
_TABLE_NAME = "financial_statements"


def _column_exists(bind, table: str, column: str) -> bool:
    return (
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
            (table, column),
        ).first()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, _TABLE_NAME, _COLUMN_NAME):
        op.add_column(
            _TABLE_NAME,
            sa.Column(
                _COLUMN_NAME,
                sa.String(length=20),
                nullable=True,
                comment="monthly | quarterly | annual | custom — extracted by LLM "
                "or inferred from period span",
            ),
        )

    # Backfill existing rows. Same thresholds as `infer_frequency()` so
    # behaviour stays consistent between historical and new uploads.
    op.execute("""
        UPDATE financial_statements
        SET frequency = CASE
            WHEN period_start IS NULL OR period_end IS NULL
                THEN NULL
            WHEN (period_end::date - period_start::date) >= 300 THEN 'annual'
            WHEN (period_end::date - period_start::date) >= 80  THEN 'quarterly'
            WHEN (period_end::date - period_start::date) >= 25  THEN 'monthly'
            WHEN (period_end::date - period_start::date) >= 0   THEN 'custom'
            ELSE NULL
        END
        WHERE frequency IS NULL
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, _TABLE_NAME, _COLUMN_NAME):
        op.drop_column(_TABLE_NAME, _COLUMN_NAME)
