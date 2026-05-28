"""add tipo_iva column to transactions_posted

Enables correct Art. 490 ET prorrateo of IVA descontable when the taxpayer
mixes operations subject to different IVA regimes. See
`app/services/tax_constants.py` for the vocabulary.

Idempotent: checks information_schema.columns before ALTER (both ways).

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-05-24 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "x4y5z6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VALID_TIPOS = (
    "gravado_19",
    "gravado_5",
    "exento",
    "excluido",
    "exportacion",
    "no_gravado",
)

_CHECK_NAME = "transactions_posted_tipo_iva_check"


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


def _constraint_exists(bind, name: str) -> bool:
    result = bind.exec_driver_sql(
        """
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = 'public' AND constraint_name = %s
        """,
        (name,),
    ).first()
    return result is not None


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "transactions_posted", "tipo_iva"):
        op.execute(
            "ALTER TABLE transactions_posted ADD COLUMN tipo_iva VARCHAR(20) NULL"
        )
    if not _constraint_exists(bind, _CHECK_NAME):
        values_sql = ", ".join(f"'{v}'" for v in _VALID_TIPOS)
        op.execute(
            f"ALTER TABLE transactions_posted "
            f"ADD CONSTRAINT {_CHECK_NAME} "
            f"CHECK (tipo_iva IS NULL OR tipo_iva IN ({values_sql}))"
        )
    # Index for filtered aggregations in F300 builder.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_transactions_posted_tipo_iva "
        "ON transactions_posted (tipo_iva)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_transactions_posted_tipo_iva")
    if _constraint_exists(bind, _CHECK_NAME):
        op.execute(f"ALTER TABLE transactions_posted DROP CONSTRAINT {_CHECK_NAME}")
    if _column_exists(bind, "transactions_posted", "tipo_iva"):
        op.execute("ALTER TABLE transactions_posted DROP COLUMN tipo_iva")
