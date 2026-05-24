"""add_draft_workflow_fields — reviewed/filed/reopen columns on tax_declaration_drafts

Adds audit trail columns for the draft → reviewed → filed workflow.
All DDL is idempotent via information_schema checks.

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-05-24 18:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2w3x4y5z6a7"
down_revision: Union[str, None] = "u1v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "tax_declaration_drafts"

_NEW_COLUMNS = [
    ("reviewed_by", "VARCHAR(128)"),
    ("reviewed_at", "TIMESTAMPTZ"),
    ("filed_by", "VARCHAR(128)"),
    ("filed_at", "TIMESTAMPTZ"),
    ("dian_acknowledgment", "VARCHAR(64)"),
    ("reopened_at", "TIMESTAMPTZ"),
    ("reopened_by", "VARCHAR(128)"),
    ("reopen_reason", "TEXT"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for col_name, col_type in _NEW_COLUMNS:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": _TABLE, "c": col_name},
        ).fetchone()
        if not exists:
            op.add_column(
                _TABLE,
                sa.Column(col_name, sa.text(col_type), nullable=True),
            )


def downgrade() -> None:
    for col_name, _ in reversed(_NEW_COLUMNS):
        op.drop_column(_TABLE, col_name)
