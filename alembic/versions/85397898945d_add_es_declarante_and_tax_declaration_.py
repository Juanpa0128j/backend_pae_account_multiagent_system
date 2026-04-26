"""add_es_declarante_and_tax_declaration_drafts

Revision ID: 85397898945d
Revises: 5bc6243a55b2
Create Date: 2026-04-22 20:37:28.253080

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "85397898945d"
down_revision: Union[str, Sequence[str], None] = "5bc6243a55b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :name"
        ),
        {"name": name},
    )
    return result.fetchone() is not None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = :table_name "
            "AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade schema."""
    # Keep migration idempotent because some CI/preview DBs already contain
    # these objects from previous runs or manual bootstrap.
    if not _table_exists("tax_declaration_drafts"):
        op.create_table(
            "tax_declaration_drafts",
            sa.Column("id", sa.String(length=50), nullable=False),
            sa.Column("company_nit", sa.String(length=20), nullable=False),
            sa.Column(
                "form_type",
                sa.String(length=10),
                nullable=False,
                comment="F300 | F350 | F110 | ICA | F220",
            ),
            sa.Column(
                "period_start",
                sa.String(length=10),
                nullable=False,
                comment="ISO date YYYY-MM-DD",
            ),
            sa.Column(
                "period_end",
                sa.String(length=10),
                nullable=False,
                comment="ISO date YYYY-MM-DD",
            ),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                comment="draft | reviewed | filed",
            ),
            sa.Column(
                "fields_json",
                sa.JSON().with_variant(
                    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
                ),
                nullable=False,
                comment="List of {renglon, label, value, source, confidence, requires_review}",
            ),
            sa.Column(
                "warnings_json",
                sa.JSON().with_variant(
                    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
                ),
                nullable=False,
                comment="List of {field, message} for fields that need accountant review",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["company_nit"], ["company_settings.nit"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tax_declaration_drafts_company_nit "
        "ON tax_declaration_drafts (company_nit)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tax_declaration_drafts_id "
        "ON tax_declaration_drafts (id)"
    )
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_created_at")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_messages_id ON chat_messages (id)")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_created_at")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chat_sessions_id ON chat_sessions (id)")

    if _table_exists("company_settings") and not _column_exists(
        "company_settings", "es_declarante"
    ):
        op.add_column(
            "company_settings",
            sa.Column(
                "es_declarante",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
                comment="True=declarante de renta (lower retefuente rates), False=no declarante",
            ),
        )

    op.alter_column(
        "company_settings",
        "tasa_ica",
        existing_type=sa.NUMERIC(precision=10, scale=8),
        comment="Tarifa ICA sobre ingresos brutos (Ley 14/1983). Varía por municipio/CIIU.",
        existing_nullable=False,
        existing_server_default=sa.text("0.00690000"),
    )
    op.alter_column(
        "company_settings",
        "tasa_renta",
        existing_type=sa.NUMERIC(precision=8, scale=6),
        comment="Tarifa impuesto de renta societario - Art. 240 ET, 35% (Ley 2277/2022).",
        existing_nullable=False,
        existing_server_default=sa.text("0.350000"),
    )
    op.alter_column(
        "financial_statements",
        "source_mode",
        existing_type=sa.VARCHAR(length=20),
        comment="direct | derived | derived_from_journal",
        existing_comment="direct | derived",
        existing_nullable=False,
        existing_server_default=sa.text("'direct'::character varying"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "financial_statements",
        "source_mode",
        existing_type=sa.VARCHAR(length=20),
        comment="direct | derived",
        existing_comment="direct | derived | derived_from_journal",
        existing_nullable=False,
        existing_server_default=sa.text("'direct'::character varying"),
    )
    op.alter_column(
        "company_settings",
        "tasa_renta",
        existing_type=sa.NUMERIC(precision=8, scale=6),
        comment=None,
        existing_comment="Tarifa impuesto de renta societario - Art. 240 ET, 35% (Ley 2277/2022).",
        existing_nullable=False,
        existing_server_default=sa.text("0.350000"),
    )
    op.alter_column(
        "company_settings",
        "tasa_ica",
        existing_type=sa.NUMERIC(precision=10, scale=8),
        comment=None,
        existing_comment="Tarifa ICA sobre ingresos brutos (Ley 14/1983). Varía por municipio/CIIU.",
        existing_nullable=False,
        existing_server_default=sa.text("0.00690000"),
    )

    if _table_exists("company_settings") and _column_exists(
        "company_settings", "es_declarante"
    ):
        op.drop_column("company_settings", "es_declarante")

    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_id")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_created_at "
        "ON chat_sessions (created_at)"
    )
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_id")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_messages_created_at "
        "ON chat_messages (created_at)"
    )

    if _table_exists("tax_declaration_drafts"):
        op.execute("DROP INDEX IF EXISTS ix_tax_declaration_drafts_id")
        op.execute("DROP INDEX IF EXISTS ix_tax_declaration_drafts_company_nit")
        op.drop_table("tax_declaration_drafts")
