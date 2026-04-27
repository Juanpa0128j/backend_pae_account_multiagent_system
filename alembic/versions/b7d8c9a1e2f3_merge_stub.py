"""Merge CI stub d8e9f0a1b2c3 with audit_logs head c7d8e9f0a1b2

Revision ID: b7d8c9a1e2f3
Revises: d8e9f0a1b2c3, c7d8e9f0a1b2
Create Date: 2026-04-26

Note: 85397898945d is NOT a parent here. At merge time it is already an
ancestor of c7d8e9f0a1b2 (chain 5bc6243a55b2 -> 85397898945d ->
c7d8e9f0a1b2), so listing it triggers a KeyError when alembic tries to
remove it from the heads set twice. The two real heads being merged are
d8e9f0a1b2c3 (CI stub off 5bc6243a55b2) and c7d8e9f0a1b2 (audit_logs).
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b7d8c9a1e2f3"
down_revision: Union[str, Sequence[str], None] = (
    "d8e9f0a1b2c3",
    "c7d8e9f0a1b2",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
