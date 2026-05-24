"""add_uvt_and_tax_base_minima_tables

Adds uvt_values and tax_base_minima tables for storing yearly UVT values
and base mínima thresholds per concepto. Both tables have hardcoded constants
in tributario_agent.py as fallback when no DB row exists.

Idempotent: checks information_schema.tables before creating.

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-05-24 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "r6s7t8u9v0w1"
down_revision: Union[str, Sequence[str], None] = "q5r6s7t8u9v0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    uvt_exists = bind.exec_driver_sql("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'uvt_values'
    """).first()

    if not uvt_exists:
        op.execute("""
            CREATE TABLE uvt_values (
                year INT PRIMARY KEY,
                value NUMERIC(12, 2) NOT NULL,
                decreto VARCHAR(64),
                effective_from DATE,
                effective_to DATE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    base_minima_exists = bind.exec_driver_sql("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'tax_base_minima'
    """).first()

    if not base_minima_exists:
        op.execute("""
            CREATE TABLE tax_base_minima (
                id SERIAL PRIMARY KEY,
                concepto VARCHAR(64) NOT NULL,
                uvt_units NUMERIC(8, 2) NOT NULL,
                year INT NOT NULL,
                effective_from DATE,
                effective_to DATE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (concepto, year)
            )
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tax_base_minima")
    op.execute("DROP TABLE IF EXISTS uvt_values")
