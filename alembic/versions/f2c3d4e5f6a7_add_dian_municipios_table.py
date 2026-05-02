"""add dian_municipios lookup table

Revision ID: f2c3d4e5f6a7
Revises: f1b2c3d4e5f6
Create Date: 2026-04-27

Adds the official DIAN municipality codes table (~1.120 entries) extracted
from data/Codigos_municipios_DIAN.pdf. Used as a deterministic lookup for:
- validating ``codigo_municipio`` in tax declaration drafts (F300/F350/F110/ICA)
- enriching transactions and reports with department/municipality names
- linking ``reteica_tarifas.municipio`` (today a lowercase string) to a
  stable DIAN code in future migrations.

Schema:
- codigo VARCHAR(5) PK — DIAN code, format XXYYY (XX=DANE department, YYY=municipality).
- nombre VARCHAR(255) — uppercase municipality name as published by DIAN.
- departamento_codigo VARCHAR(2) — DANE department prefix.
- departamento_nombre VARCHAR(100) — readable department name.

Idempotent: uses CREATE TABLE / INDEX IF NOT EXISTS so re-running is safe.
Seed data is loaded by ``scripts/seed_dian_municipios.py`` (separate, idempotent).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f2c3d4e5f6a7"
down_revision = "f1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS dian_municipios (
            codigo               VARCHAR(5)  PRIMARY KEY,
            nombre               VARCHAR(255) NOT NULL,
            departamento_codigo  VARCHAR(2)  NOT NULL,
            departamento_nombre  VARCHAR(100) NOT NULL
        )
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_dian_municipios_departamento
            ON dian_municipios (departamento_codigo)
        """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_dian_municipios_nombre_gin
            ON dian_municipios USING GIN (to_tsvector('simple', nombre))
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_dian_municipios_nombre_gin")
    op.execute("DROP INDEX IF EXISTS ix_dian_municipios_departamento")
    op.execute("DROP TABLE IF EXISTS dian_municipios")
