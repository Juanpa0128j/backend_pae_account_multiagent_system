"""
Force-stamp the alembic_version table to a known revision.

Use when the DB has a phantom revision (applied outside of alembic)
that doesn't exist in the local migrations chain, causing alembic to fail.

Usage:
    uv run python scripts/fix_alembic_stamp.py [revision]

Default revision: f3a4b5c6d7e8 (current head of this branch)
"""

import sys

from sqlalchemy import create_engine, text

from app.core.config import settings

TARGET = sys.argv[1] if len(sys.argv) > 1 else "f3a4b5c6d7e8"

engine = create_engine(settings.database_url)
with engine.connect() as conn:
    current = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    print(f"Current revision: {current[0] if current else 'none'}")
    conn.execute(text(f"UPDATE alembic_version SET version_num = '{TARGET}'"))
    conn.commit()
    print(f"Stamped to: {TARGET}")
