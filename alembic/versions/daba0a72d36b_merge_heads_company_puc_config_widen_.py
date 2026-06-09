"""merge heads: company_puc_config + widen tax_concepts

Revision ID: daba0a72d36b
Revises: 515fdf0e01b9, c9d0e1f2a3b4
Create Date: 2026-05-31 18:48:16.863811

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "daba0a72d36b"
down_revision: Union[str, Sequence[str], None] = ("515fdf0e01b9", "c9d0e1f2a3b4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
