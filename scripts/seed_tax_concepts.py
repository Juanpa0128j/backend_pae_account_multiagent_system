"""Seed F350 tax_concepts catalog (Res. DIAN 000031/2024).

Sources:
- Art. 392 ET — Retenciones por compras / servicios
- Art. 401 ET — Honorarios y comisiones
- Art. 392 / Decreto 0260/2001 — Arrendamientos
- Art. 408 ET, Res. DIAN 000031/2024 — Hidrocarburos, carbón, minerales
- Art. 20-3 ET (Ley 2277/2022) — Presencia Económica Significativa (PES)
- Art. 383 ET — Retenciones sobre salarios
- Ley 14/1983 — ReteICA

Run: uv run python scripts/seed_tax_concepts.py

Idempotent — upserts keyed by code.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.services.db_service import upsert_tax_concept

logger = get_logger(__name__)

# (code, label, renglon_350, aplica_a, categoria, tarifa, base_uvt, art)
ROWS = [
    # ── Compras (Art. 392 ET) ────────────────────────────────────────────────
    (
        "compras_pj",
        "Compras a personas jurídicas",
        "25",
        "PJ",
        "compras",
        Decimal("0.0250"),
        Decimal("27"),
        "Art. 392 ET",
    ),
    (
        "compras_pn",
        "Compras a personas naturales",
        "27",
        "PN",
        "compras",
        Decimal("0.0250"),
        Decimal("27"),
        "Art. 392 ET",
    ),
    # ── Servicios (Art. 392 ET) ──────────────────────────────────────────────
    (
        "servicios_pj",
        "Servicios — personas jurídicas declarantes",
        "28",
        "PJ",
        "servicios",
        Decimal("0.0400"),
        Decimal("4"),
        "Art. 392 ET",
    ),
    (
        "serv_pn_decl",
        "Servicios — personas naturales declarantes",
        "29",
        "PN",
        "servicios",
        Decimal("0.0400"),
        Decimal("4"),
        "Art. 392 ET",
    ),
    (
        "serv_pn_no_decl",
        "Servicios — personas naturales no declarantes",
        "30",
        "PN",
        "servicios",
        Decimal("0.0600"),
        Decimal("4"),
        "Art. 392 ET",
    ),
    # ── Honorarios y comisiones (Art. 401 ET) ────────────────────────────────
    (
        "honorarios_pj",
        "Honorarios — personas jurídicas",
        "32",
        "PJ",
        "honorarios",
        Decimal("0.1100"),
        Decimal("0"),
        "Art. 401 ET",
    ),
    (
        "honorarios_pn",
        "Honorarios — personas naturales",
        "33",
        "PN",
        "honorarios",
        Decimal("0.1000"),
        Decimal("0"),
        "Art. 401 ET",
    ),
    # ── Arrendamientos (Decreto 0260/2001) ───────────────────────────────────
    (
        "arrendamiento_pj",
        "Arrendamiento — personas jurídicas",
        "34",
        "PJ",
        "arrendamiento",
        Decimal("0.0350"),
        Decimal("27"),
        "Decreto 0260/2001",
    ),
    (
        "arrendamiento_pn",
        "Arrendamiento — personas naturales",
        "35",
        "PN",
        "arrendamiento",
        Decimal("0.0350"),
        Decimal("27"),
        "Decreto 0260/2001",
    ),
    # ── Hidrocarburos / minerales (Art. 408 ET, Res. 000031/2024) ────────────
    (
        "hidrocarburos",
        "Compra de hidrocarburos",
        "40",
        "AMB",
        "hidrocarburos",
        Decimal("0.0050"),
        Decimal("0"),
        "Art. 408 ET",
    ),
    (
        "carbon",
        "Compra de carbón",
        "41",
        "AMB",
        "minerales",
        Decimal("0.0010"),
        Decimal("0"),
        "Res. DIAN 000031/2024",
    ),
    (
        "minerales",
        "Compra de otros minerales",
        "42",
        "AMB",
        "minerales",
        Decimal("0.0240"),
        Decimal("0"),
        "Res. DIAN 000031/2024",
    ),
    # ── Presencia Económica Significativa (Art. 20-3 ET, Ley 2277/2022) ──────
    (
        "pes_svcs_dig",
        "PES — servicios digitales",
        "65",
        "AMB",
        "pes",
        Decimal("0.1000"),
        Decimal("0"),
        "Art. 20-3 ET",
    ),
    (
        "pes_pub_online",
        "PES — publicidad online",
        "66",
        "AMB",
        "pes",
        Decimal("0.1000"),
        Decimal("0"),
        "Art. 20-3 ET",
    ),
    # ── Salarios (Art. 383 ET) ───────────────────────────────────────────────
    (
        "salarios_383",
        "Retenciones sobre salarios — Art. 383 ET",
        "50",
        "PN",
        "salarios",
        None,
        None,
        "Art. 383 ET",
    ),
    # ── ReteICA ──────────────────────────────────────────────────────────────
    (
        "reteica",
        "Retención ICA practicada",
        "76",
        "AMB",
        "ica",
        None,
        None,
        "Ley 14/1983",
    ),
]


def main() -> None:
    db = SessionLocal()
    try:
        upserted = 0
        for code, label, renglon, aplica_a, categoria, tarifa, base, art in ROWS:
            row = upsert_tax_concept(
                db,
                code=code,
                label=label,
                renglon_350=renglon,
                aplica_a=aplica_a,
                categoria=categoria,
                tarifa_default=tarifa,
                base_minima_uvt=base,
                art_referencia=art,
            )
            logger.info(
                "seed_tax_concepts: upserted %s → renglón %s (%s) [%s]",
                row.code,
                row.renglon_350,
                row.aplica_a,
                row.art_referencia,
            )
            upserted += 1
        logger.info("seed_tax_concepts: done — %d rows upserted.", upserted)
    except Exception:
        logger.exception("seed_tax_concepts: failed")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
