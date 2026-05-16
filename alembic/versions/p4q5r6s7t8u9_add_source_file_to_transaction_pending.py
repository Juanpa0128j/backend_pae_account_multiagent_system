"""add_source_file_to_transaction_pending

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-05-14 23:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p4q5r6s7t8u9"
down_revision: Union[str, Sequence[str], None] = "o3p4q5r6s7t8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions_pending",
        sa.Column(
            "source_file",
            sa.String(500),
            nullable=True,
            comment="Filename of the source document (documents multi-file mode only)",
        ),
    )


def downgrade() -> None:
    op.drop_column("transactions_pending", "source_file")
