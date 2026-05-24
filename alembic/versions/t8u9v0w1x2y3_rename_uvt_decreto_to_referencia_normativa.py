"""rename uvt_values.decreto -> uvt_values.referencia_normativa

UVT is published by DIAN via Resolución (e.g. Resolución 000238 for 2026),
not by Decreto. Rename the column to reflect the correct legal instrument.

Idempotent: checks information_schema.columns before ALTER (both directions).

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-05-24 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "t8u9v0w1x2y3"
down_revision: Union[str, Sequence[str], None] = "s7t8u9v0w1x2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    result = bind.exec_driver_sql(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    ).first()
    return result is not None


def upgrade() -> None:
    bind = op.get_bind()
    has_old = _column_exists(bind, "uvt_values", "decreto")
    has_new = _column_exists(bind, "uvt_values", "referencia_normativa")

    if has_old and not has_new:
        op.execute(
            "ALTER TABLE uvt_values RENAME COLUMN decreto TO referencia_normativa"
        )


def downgrade() -> None:
    bind = op.get_bind()
    has_old = _column_exists(bind, "uvt_values", "decreto")
    has_new = _column_exists(bind, "uvt_values", "referencia_normativa")

    if has_new and not has_old:
        op.execute(
            "ALTER TABLE uvt_values RENAME COLUMN referencia_normativa TO decreto"
        )
