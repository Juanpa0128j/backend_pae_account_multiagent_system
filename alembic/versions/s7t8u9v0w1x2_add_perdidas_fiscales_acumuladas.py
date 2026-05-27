"""add_perdidas_fiscales_acumuladas

Adds perdidas_fiscales_acumuladas table for multi-year fiscal loss
carry-forward history (Art. 147 ET — 12-year carry-forward).

Idempotent: checks information_schema.tables before creating.

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-05-24 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "s7t8u9v0w1x2"
down_revision: Union[str, Sequence[str], None] = "r6s7t8u9v0w1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    table_exists = bind.exec_driver_sql("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'perdidas_fiscales_acumuladas'
    """).first()

    if not table_exists:
        op.execute("""
            CREATE TABLE perdidas_fiscales_acumuladas (
                id SERIAL PRIMARY KEY,
                company_nit VARCHAR(20) NOT NULL
                    REFERENCES company_settings(nit) ON DELETE CASCADE,
                year INT NOT NULL,
                monto_perdida NUMERIC(18, 2) NOT NULL,
                monto_compensado NUMERIC(18, 2) NOT NULL DEFAULT 0,
                monto_pendiente NUMERIC(18, 2) NOT NULL DEFAULT 0,
                decreto VARCHAR(100),
                notas TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (company_nit, year)
            )
        """)
        op.execute("""
            CREATE INDEX idx_perdidas_company
                ON perdidas_fiscales_acumuladas(company_nit)
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS perdidas_fiscales_acumuladas")
