"""add locked_pathway to company_settings

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j8k9l0m1n2o3"
down_revision: Union[str, Sequence[str], None] = "i7j8k9l0m1n2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: a parallel branch may have applied the same column already.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("company_settings")}
    if "locked_pathway" in existing:
        return
    op.add_column(
        "company_settings",
        sa.Column(
            "locked_pathway",
            sa.String(30),
            nullable=True,
            comment="'build_from_scratch' (Vía A) or 'work_with_existing' (Vía B) — set on first upload",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("company_settings")}
    if "locked_pathway" not in existing:
        return
    op.drop_column("company_settings", "locked_pathway")
