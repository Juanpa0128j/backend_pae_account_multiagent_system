"""widen user_company.user_id from VARCHAR(36) to VARCHAR(255)

Supabase user IDs can be prefixed (e.g. "user-<uuid>") making them 40+ chars.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-14 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "user_company",
        "user_id",
        existing_type=sa.String(36),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "user_company",
        "user_id",
        existing_type=sa.String(255),
        type_=sa.String(36),
        existing_nullable=False,
    )
