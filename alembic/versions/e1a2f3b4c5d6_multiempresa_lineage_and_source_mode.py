"""multiempresa lineage and source mode

Revision ID: e1a2f3b4c5d6
Revises: b232aff042b8
Create Date: 2026-03-22 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1a2f3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "b232aff042b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transactions_pending",
        sa.Column("company_nit", sa.String(length=20), nullable=True, comment="Owning company NIT (tenant)"),
    )
    op.add_column(
        "transactions_posted",
        sa.Column("company_nit", sa.String(length=20), nullable=True, comment="Owning company NIT (tenant)"),
    )
    op.add_column(
        "journal_entry_lines",
        sa.Column("company_nit", sa.String(length=20), nullable=True, comment="Owning company NIT (tenant)"),
    )
    op.add_column(
        "financial_statements",
        sa.Column(
            "source_mode",
            sa.String(length=20),
            nullable=False,
            server_default="direct",
            comment="direct | derived",
        ),
    )

    op.create_index("ix_transactions_pending_company_nit", "transactions_pending", ["company_nit"], unique=False)
    op.create_index("ix_transactions_posted_company_nit", "transactions_posted", ["company_nit"], unique=False)
    op.create_index("ix_journal_entry_lines_company_nit", "journal_entry_lines", ["company_nit"], unique=False)
    op.create_index("ix_financial_statements_entity_nit", "financial_statements", ["entity_nit"], unique=False)
    op.create_index(
        "ix_financial_statements_entity_type_period",
        "financial_statements",
        ["entity_nit", "statement_type", "period_start", "period_end"],
        unique=False,
    )

    op.create_table(
        "financial_statement_lineage",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("target_statement_id", sa.String(length=50), nullable=False),
        sa.Column("source_statement_id", sa.String(length=50), nullable=False),
        sa.Column(
            "relation_type",
            sa.String(length=30),
            nullable=False,
            server_default="input",
            comment="input | reference",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["source_statement_id"], ["financial_statements.id"]),
        sa.ForeignKeyConstraint(["target_statement_id"], ["financial_statements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_financial_statement_lineage_id", "financial_statement_lineage", ["id"], unique=False)
    op.create_index(
        "ix_financial_statement_lineage_target_statement_id",
        "financial_statement_lineage",
        ["target_statement_id"],
        unique=False,
    )
    op.create_index(
        "ix_financial_statement_lineage_source_statement_id",
        "financial_statement_lineage",
        ["source_statement_id"],
        unique=False,
    )

    # Backfill best-effort ownership for historical rows.
    op.execute(
        """
        UPDATE transactions_pending
        SET company_nit = nit_receptor
        WHERE company_nit IS NULL AND nit_receptor IS NOT NULL;
        """
    )
    op.execute(
        """
        UPDATE transactions_posted tp
        SET company_nit = tpn.company_nit
        FROM transactions_pending tpn
        WHERE tp.transaction_pending_id = tpn.id
          AND tp.company_nit IS NULL
          AND tpn.company_nit IS NOT NULL;
        """
    )
    op.execute(
        """
        UPDATE journal_entry_lines jel
        SET company_nit = tp.company_nit
        FROM transactions_posted tp
        WHERE jel.transaction_posted_id = tp.id
          AND jel.company_nit IS NULL
          AND tp.company_nit IS NOT NULL;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_financial_statement_lineage_source_statement_id", table_name="financial_statement_lineage")
    op.drop_index("ix_financial_statement_lineage_target_statement_id", table_name="financial_statement_lineage")
    op.drop_index("ix_financial_statement_lineage_id", table_name="financial_statement_lineage")
    op.drop_table("financial_statement_lineage")

    op.drop_index("ix_financial_statements_entity_type_period", table_name="financial_statements")
    op.drop_index("ix_financial_statements_entity_nit", table_name="financial_statements")
    op.drop_index("ix_journal_entry_lines_company_nit", table_name="journal_entry_lines")
    op.drop_index("ix_transactions_posted_company_nit", table_name="transactions_posted")
    op.drop_index("ix_transactions_pending_company_nit", table_name="transactions_pending")

    op.drop_column("financial_statements", "source_mode")
    op.drop_column("journal_entry_lines", "company_nit")
    op.drop_column("transactions_posted", "company_nit")
    op.drop_column("transactions_pending", "company_nit")
