"""add functional btree index on upper(nombre) for dian_municipios

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-03

The original ``f2c3d4e5f6a7_add_dian_municipios_table`` migration created a
GIN index on ``to_tsvector('simple', nombre)``, but the actual lookup in
``app/services/dian_codes.py::lookup_municipio_by_name`` runs an exact-match
query: ``WHERE upper(nombre) = upper(:nombre)``. PostgreSQL cannot use the
GIN index for that pattern, so name lookups currently degrade to sequential
scan over 1120 rows.

This migration adds a btree functional index matching the actual query
pattern. The GIN index is left in place for potential future full-text
search needs over municipality names.

Idempotent: ``CREATE INDEX IF NOT EXISTS``.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_dian_municipios_nombre_upper
            ON dian_municipios (upper(nombre))
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_dian_municipios_nombre_upper")
