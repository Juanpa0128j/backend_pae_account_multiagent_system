"""normalize legacy parser modes to v2 tiers

premium/gpt4o were removed from ParserMode when parsing moved to the
llama-cloud v2 SDK; both map to the agentic tier. Irreversible by design:
the original mode strings carry no information the new code can use.

Revision ID: n4o5p6q7r8s9
Revises: h3i4j5k6l7m8
Create Date: 2026-07-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, Sequence[str], None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE ingest_jobs SET parser_mode = 'agentic'"
        " WHERE parser_mode IN ('premium', 'gpt4o')"
    )


def downgrade() -> None:
    # Irreversible data normalization; nothing to restore.
    pass
