"""add_user_company

Revision ID: fb08f11836cb
Revises: 33c6ac057344
Create Date: 2026-05-09 07:48:11.124694

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "fb08f11836cb"
down_revision: Union[str, Sequence[str], None] = "33c6ac057344"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_company",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("company_nit", sa.String(), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_nit"], ["company_settings.nit"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "company_nit"),
    )
    op.create_index("idx_user_company_user_id", "user_company", ["user_id"])
    op.create_index("idx_user_company_company_nit", "user_company", ["company_nit"])


def downgrade() -> None:
    op.drop_index("idx_user_company_user_id", table_name="user_company")
    op.drop_index("idx_user_company_company_nit", table_name="user_company")
    op.drop_table("user_company")
