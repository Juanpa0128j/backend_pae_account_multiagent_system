"""
Seed the dian_municipios lookup table from data/dian_municipios.json.

Run after applying migration f2c3d4e5f6a7:

    python scripts/seed_dian_municipios.py            # idempotent upsert
    python scripts/seed_dian_municipios.py --truncate # wipe + reload (rare)

Source data is extracted from data/Codigos_municipios_DIAN.pdf using pypdf.
The seed is fully idempotent: each row is upserted via INSERT ... ON CONFLICT
(codigo) DO UPDATE so re-running is safe and only diverging rows update.

Why a script (not part of the Alembic migration): seed data may evolve
(new municipalities, name corrections) and we want to refresh it without
running a downgrade/upgrade cycle.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from sqlalchemy import create_engine, text  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DATA_PATH = PROJECT_ROOT / "data" / "dian_municipios.json"

UPSERT_SQL = text("""
    INSERT INTO dian_municipios (codigo, nombre, departamento_codigo, departamento_nombre)
    VALUES (:codigo, :nombre, :departamento_codigo, :departamento_nombre)
    ON CONFLICT (codigo) DO UPDATE SET
        nombre              = EXCLUDED.nombre,
        departamento_codigo = EXCLUDED.departamento_codigo,
        departamento_nombre = EXCLUDED.departamento_nombre
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed dian_municipios from JSON.")
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate the table before seeding (default: idempotent upsert).",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL is not set. Add it to .env and retry.")
        sys.exit(1)

    if not DATA_PATH.exists():
        logger.error(
            "Seed file not found: %s. Re-extract from Codigos_municipios_DIAN.pdf.",
            DATA_PATH,
        )
        sys.exit(1)

    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    logger.info("Loaded %d municipalities from %s", len(rows), DATA_PATH.name)

    engine = create_engine(db_url)
    with engine.begin() as conn:
        if args.truncate:
            logger.warning("--truncate: wiping dian_municipios before reseed.")
            conn.execute(text("TRUNCATE TABLE dian_municipios"))

        batch_size = 200
        inserted = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(UPSERT_SQL, batch)
            inserted += len(batch)
            logger.info("  Upserted %d / %d", inserted, len(rows))

    with engine.connect() as conn:
        total = conn.execute(text("SELECT count(*) FROM dian_municipios")).scalar()
        logger.info("Done. dian_municipios now has %d rows.", total)


if __name__ == "__main__":
    main()
