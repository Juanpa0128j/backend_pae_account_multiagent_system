"""merge_heads_after_pending_audit_review

Revision ID: 33c6ac057344
Revises: a3b4c5d6e7f8, g5h6i7j8k9l0
Create Date: 2026-05-03 10:10:37.922504

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "33c6ac057344"
down_revision: Union[str, Sequence[str], None] = ("a3b4c5d6e7f8", "h6i7j8k9l0m1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
