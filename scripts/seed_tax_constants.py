"""Seed UVT values and base mínima thresholds for 2024–2026.

Sources:
- UVT 2024: $47,065 — Decreto 1235/2023
- UVT 2025: $49,799 — Decreto 2229/2024
- UVT 2026: $52,374 — Decreto 0024/2025

Base mínima temporal windows (Fix 3 — Decreto 572 temporal effect):
  • 2024-01-01 → 2025-05-31: pre-Decreto 572 values (Art. 392/401 ET standard)
      retefuente_servicios: 4 UVT, retefuente_bienes/arrendamiento: 27 UVT, reteica: 4 UVT
  • 2025-06-01 → 2026-05-07: Decreto 572 window
      servicios: 2 UVT (TBD — verify DIAN circular), bienes: 10 UVT (TBD)
      NOTE: reteica NOT affected — municipal, not national; base stays at 4 UVT
  • 2026-05-08 → NULL (current): post-Decreto 572 suspension (Consejo de Estado May 7 2026)
      Back to standard Art. 392/401 ET: servicios=4 UVT, bienes/arrendamiento=27 UVT

TBD values marked below — verify with DIAN circular when Decreto 572 is re-published
or Consejo de Estado confirms final suspension.

Run: DATABASE_URL=... uv run python scripts/seed_tax_constants.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from decimal import Decimal

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.database import TaxBaseMinima, UvtValue
from app.services import db_service

logger = get_logger(__name__)

UVT_DATA = [
    # (year, value, referencia_normativa)
    (2024, Decimal("47065"), "Resolución 000187 de 2023"),
    (2025, Decimal("49799"), "Resolución 000193 de 2024"),
    # UVT 2026 fixed by DIAN Resolución 000238 de 2025 (no longer by Decreto)
    (2026, Decimal("52374"), "Resolución 000238 de 2025"),
]

# ---------------------------------------------------------------------------
# Temporal base mínima rows (concepto, uvt_units, year, effective_from, effective_to)
# ---------------------------------------------------------------------------
# Window 1: 2024-01-01 → 2025-05-31 (pre Decreto 572)
# Window 2: 2025-06-01 → 2026-05-07 (Decreto 572 in effect)
# Window 3: 2026-05-08 → None (post-suspension — standard ET values restored)
#
# TBD: Decreto 572 servicios/bienes values not yet confirmed by DIAN circular.
# Using 2 UVT (servicios) / 10 UVT (bienes) as reported in press coverage.
# Verify and update when Consejo de Estado resolution is published.
BASE_MINIMA_TEMPORAL = [
    # ── Window 1: pre-Decreto 572 (2024-2025 standard) ────────────────────
    ("retefuente_servicios", Decimal("4"), 2025, date(2024, 1, 1), date(2025, 5, 31)),
    ("retefuente_bienes", Decimal("27"), 2025, date(2024, 1, 1), date(2025, 5, 31)),
    (
        "retefuente_arrendamiento",
        Decimal("27"),
        2025,
        date(2024, 1, 1),
        date(2025, 5, 31),
    ),
    ("reteica", Decimal("4"), 2025, date(2024, 1, 1), date(2025, 5, 31)),
    # ── Window 2: Decreto 572 (Jun 2025 – May 7 2026) ──────────────────────
    # TBD: servicios=2 UVT, bienes=10 UVT per press coverage of Decreto 572.
    # reteica: municipal — Decreto 572 does NOT apply; stays at 4 UVT reference.
    (
        "retefuente_servicios",
        Decimal("2"),
        2025,
        date(2025, 6, 1),
        date(2026, 5, 7),
    ),  # TBD
    (
        "retefuente_bienes",
        Decimal("10"),
        2025,
        date(2025, 6, 1),
        date(2026, 5, 7),
    ),  # TBD
    (
        "retefuente_arrendamiento",
        Decimal("10"),
        2025,
        date(2025, 6, 1),
        date(2026, 5, 7),
    ),  # TBD
    ("reteica", Decimal("4"), 2025, date(2025, 6, 1), date(2026, 5, 7)),
    (
        "retefuente_servicios",
        Decimal("2"),
        2026,
        date(2025, 6, 1),
        date(2026, 5, 7),
    ),  # TBD
    (
        "retefuente_bienes",
        Decimal("10"),
        2026,
        date(2025, 6, 1),
        date(2026, 5, 7),
    ),  # TBD
    (
        "retefuente_arrendamiento",
        Decimal("10"),
        2026,
        date(2025, 6, 1),
        date(2026, 5, 7),
    ),  # TBD
    ("reteica", Decimal("4"), 2026, date(2025, 6, 1), date(2026, 5, 7)),
    # ── Window 3: post-suspension (2026-05-08 onward — standard ET restored) ─
    ("retefuente_servicios", Decimal("4"), 2026, date(2026, 5, 8), None),
    ("retefuente_bienes", Decimal("27"), 2026, date(2026, 5, 8), None),
    ("retefuente_arrendamiento", Decimal("27"), 2026, date(2026, 5, 8), None),
    ("reteica", Decimal("4"), 2026, date(2026, 5, 8), None),
    # ── Legacy year-only rows (no effective_from) for backward compat ───────
    # These serve the year-based fallback in get_base_minima(as_of_date=None).
    ("retefuente_servicios", Decimal("4"), 2024, None, None),
    ("retefuente_bienes", Decimal("27"), 2024, None, None),
    ("retefuente_arrendamiento", Decimal("27"), 2024, None, None),
    ("reteica", Decimal("4"), 2024, None, None),
]


def seed() -> None:
    db = SessionLocal()
    uvt_inserted = 0
    uvt_updated = 0
    bm_inserted = 0
    bm_updated = 0

    try:
        # ── UVT values ─────────────────────────────────────────────────────
        for year, value, referencia_normativa in UVT_DATA:
            existing = db.query(UvtValue).filter(UvtValue.year == year).first()
            db_service.upsert_uvt(
                db,
                year=year,
                value=value,
                referencia_normativa=referencia_normativa,
            )
            if existing:
                uvt_updated += 1
                logger.info(
                    "UVT %d updated: %s (%s)", year, value, referencia_normativa
                )
            else:
                uvt_inserted += 1
                logger.info(
                    "UVT %d inserted: %s (%s)", year, value, referencia_normativa
                )

        # ── Temporal base mínima rows ───────────────────────────────────────
        for concepto, uvt_units, year, eff_from, eff_to in BASE_MINIMA_TEMPORAL:
            # Composite key for upsert: (concepto, year, effective_from)
            q = db.query(TaxBaseMinima).filter(
                TaxBaseMinima.concepto == concepto,
                TaxBaseMinima.year == year,
            )
            if eff_from is not None:
                q = q.filter(TaxBaseMinima.effective_from == eff_from)
            else:
                q = q.filter(TaxBaseMinima.effective_from.is_(None))

            existing = q.first()
            if existing:
                existing.uvt_units = uvt_units
                existing.effective_to = eff_to
                db.flush()
                bm_updated += 1
                logger.info(
                    "Base mínima %s/%d [%s→%s] updated: %s UVT",
                    concepto,
                    year,
                    eff_from,
                    eff_to,
                    uvt_units,
                )
            else:
                row = TaxBaseMinima(
                    concepto=concepto,
                    uvt_units=uvt_units,
                    year=year,
                    effective_from=eff_from,
                    effective_to=eff_to,
                )
                db.add(row)
                bm_inserted += 1
                logger.info(
                    "Base mínima %s/%d [%s→%s] inserted: %s UVT",
                    concepto,
                    year,
                    eff_from,
                    eff_to,
                    uvt_units,
                )

        db.commit()
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
