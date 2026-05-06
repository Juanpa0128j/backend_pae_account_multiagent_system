"""add_pending_audit_review_to_process_status

Revision ID: g5h6i7j8k9l0
Revises: f4c5d6e7a8b9
Create Date: 2026-05-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "g5h6i7j8k9l0"
down_revision: Union[str, Sequence[str], None] = "f4c5d6e7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add both cases: existing DB enum uses uppercase member names (QUEUED, RUNNING, etc.)
    op.execute(
        "ALTER TYPE processstatus ADD VALUE IF NOT EXISTS 'PENDING_AUDIT_REVIEW'"
    )
    op.execute(
        "ALTER TYPE processstatus ADD VALUE IF NOT EXISTS 'pending_audit_review'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    pass
