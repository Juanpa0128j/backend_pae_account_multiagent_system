"""Seed UVT values and base mínima thresholds for 2024–2026.

Sources:
- UVT 2024: $47,065 — Decreto 1235/2023
- UVT 2025: $49,799 — Decreto 2229/2024
- UVT 2026: $52,374 — Decreto 0024/2025

Base mínima (Art. 392 / 401 ET):
- retefuente_servicios:     4 UVT/mes
- retefuente_bienes:       27 UVT/mes
- retefuente_arrendamiento: 27 UVT/mes
- reteica:                  4 UVT/mes (referencia conservadora)

Run: DATABASE_URL=... uv run python scripts/seed_tax_constants.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.services import db_service

logger = get_logger(__name__)

UVT_DATA = [
    # (year, value, decreto)
    (2024, Decimal("47065"), "Decreto 1235/2023"),
    (2025, Decimal("49799"), "Decreto 2229/2024"),
    (2026, Decimal("52374"), "Decreto 0024/2025"),
]

# concepto → uvt_units (same for all years 2024-2026 per ET)
BASE_MINIMA_DATA = [
    ("retefuente_servicios", Decimal("4")),
    ("retefuente_bienes", Decimal("27")),
    ("retefuente_arrendamiento", Decimal("27")),
    ("reteica", Decimal("4")),
]

YEARS = [2024, 2025, 2026]


def seed() -> None:
    db = SessionLocal()
    uvt_inserted = 0
    uvt_updated = 0
    bm_inserted = 0
    bm_updated = 0

    try:
        for year, value, decreto in UVT_DATA:
            from app.models.database import UvtValue

            existing = db.query(UvtValue).filter(UvtValue.year == year).first()
            db_service.upsert_uvt(db, year=year, value=value, decreto=decreto)
            if existing:
                uvt_updated += 1
                logger.info("UVT %d updated: %s (%s)", year, value, decreto)
            else:
                uvt_inserted += 1
                logger.info("UVT %d inserted: %s (%s)", year, value, decreto)

        for year in YEARS:
            for concepto, uvt_units in BASE_MINIMA_DATA:
                from app.models.database import TaxBaseMinima

                existing = (
                    db.query(TaxBaseMinima)
                    .filter(
                        TaxBaseMinima.concepto == concepto,
                        TaxBaseMinima.year == year,
                    )
                    .first()
                )
                db_service.upsert_base_minima(
                    db, concepto=concepto, uvt_units=uvt_units, year=year
                )
                if existing:
                    bm_updated += 1
                    logger.info(
                        "Base mínima %s/%d updated: %s UVT", concepto, year, uvt_units
                    )
                else:
                    bm_inserted += 1
                    logger.info(
                        "Base mínima %s/%d inserted: %s UVT", concepto, year, uvt_units
                    )

        print(
            f"Done: UVT {uvt_inserted} inserted / {uvt_updated} updated, "
            f"base mínima {bm_inserted} inserted / {bm_updated} updated."
        )
    except Exception as e:
        db.rollback()
        logger.error("Seed failed: %s", e)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
