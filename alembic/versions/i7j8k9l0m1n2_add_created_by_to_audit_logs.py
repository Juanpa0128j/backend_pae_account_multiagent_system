"""add_created_by_to_audit_logs

Revision ID: i7j8k9l0m1n2
Revises: fb08f11836cb
Create Date: 2026-05-09 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "i7j8k9l0m1n2"
down_revision: Union[str, Sequence[str], None] = "fb08f11836cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("created_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "created_by")
