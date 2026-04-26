"""Merge missing CI revision d8e9f0a1b2c3 with main branch head 85397898945d

Revision ID: b7d8c9a1e2f3
Revises: d8e9f0a1b2c3, 85397898945d
Create Date: 2026-04-26

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'b7d8c9a1e2f3'
down_revision: Union[str, Sequence[str], None] = ('d8e9f0a1b2c3', '85397898945d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
