"""add locked_pathway to company_settings

Revision ID: h6i7j8k9l0m1
Revises: g5h6i7j8k9l0
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h6i7j8k9l0m1"
down_revision: Union[str, Sequence[str], None] = "g5h6i7j8k9l0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.drop_column("company_settings", "locked_pathway")
