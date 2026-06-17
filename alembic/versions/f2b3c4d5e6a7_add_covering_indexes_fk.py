"""add covering indexes for unindexed foreign keys

company_puc_config.cuenta_codigo and user_company.company_nit are each the
SECOND column of a composite PK, so neither is the leading column of any index —
the Supabase linter flags them as unindexed foreign keys (slower FK integrity
checks and joins/filters on those columns as data grows). Add a dedicated
single-column covering index for each.

Idempotent (IF NOT EXISTS) and non-destructive — safe to re-run.

Revision ID: f2b3c4d5e6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index_name, table, column)
_INDEXES = [
    ("ix_company_puc_config_cuenta_codigo", "company_puc_config", "cuenta_codigo"),
    ("ix_user_company_company_nit", "user_company", "company_nit"),
]


def upgrade() -> None:
    for index_name, table, column in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")


def downgrade() -> None:
    for index_name, _table, _column in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
