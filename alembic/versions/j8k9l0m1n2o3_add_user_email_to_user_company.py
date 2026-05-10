"""add user_email to user_company for cross-signup re-association

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
Create Date: 2026-05-10 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "j8k9l0m1n2o3"
down_revision: Union[str, Sequence[str], None] = "i7j8k9l0m1n2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_company",
        sa.Column("user_email", sa.String(length=320), nullable=True),
    )
    op.create_index(
        "ix_user_company_user_email",
        "user_company",
        ["user_email"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_company_user_email", table_name="user_company")
    op.drop_column("user_company", "user_email")
