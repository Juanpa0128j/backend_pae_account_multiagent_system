"""Seed regulatory income-tax rate table (tarifas_renta) for Colombian Renta PJ.

Sources:
- Art. 240 ET (Ley 2277/2022): tarifa general 35% desde año fiscal 2023
- Art. 240 par. 4 ET: sector financiero sobretasa 5% (2023-2027)
- Decreto 0150/2026 emergencia económica: sector financiero sobretasa adicional 20% (sólo 2026)
- Art. 240 par. 5 ET: hidroeléctricas > 30 MW sobretasa 3% (2023-2026)
- Art. 19 ET: entidades sin ánimo de lucro (ESAL) 20%
- Art. 240-1 ET: zonas francas y RST 20%

Run: uv run python scripts/seed_tarifas_renta.py

Idempotent — upserts keyed by (regimen, actividad, year_from).
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.services.db_service import upsert_tarifa_renta

logger = get_logger(__name__)

# (regimen, actividad, year_from, year_to, tarifa_base, sobretasa, base_legal, notas)
ROWS = [
    # ── Régimen ordinario — tarifa general 35% (Art. 240 ET, Ley 2277/2022) ──
    (
        "ordinario",
        "general",
        2023,
        None,
        Decimal("0.3500"),
        Decimal("0"),
        "Art. 240 ET (Ley 2277/2022)",
        "Tarifa general sociedades colombianas desde año fiscal 2023.",
    ),
    # ── Régimen ordinario — sector financiero sobretasa 5% permanente (2023-2027) ──
    # Art. 240 par. 4 ET: bancos, compañías de seguros, comisionistas, fiduciarias, etc.
    (
        "ordinario",
        "financiero",
        2023,
        2027,
        Decimal("0.3500"),
        Decimal("0.0500"),
        "Art. 240 par. 4 ET",
        "Sobretasa permanente 5% sector financiero (2023-2027). Sujetos: bancos, seguros, comisionistas, fiduciarias, sociedades administradoras.",
    ),
    # ── Régimen ordinario — sector financiero sobretasa emergencia Decreto 0150/2026 ──
    # Decreto 0150 de 2026 (emergencia económica): sobretasa adicional 20% para año 2026.
    # Esta fila tiene year_from=2026, year_to=2026 — mayor especificidad que la anterior.
    # get_tarifa_renta ordena por year_from DESC y devuelve la más específica.
    (
        "ordinario",
        "financiero",
        2026,
        2026,
        Decimal("0.3500"),
        Decimal("0.2000"),
        "Decreto 0150/2026 emergencia económica",
        "Sobretasa adicional 20% sector financiero año fiscal 2026 (Decreto 0150/2026). Reemplaza la sobretasa del par. 4 para este año — tarifa efectiva 55%.",
    ),
    # ── Régimen ordinario — hidroeléctricas > 30 MW sobretasa 3% (2023-2026) ──
    # Art. 240 par. 5 ET: generadoras hidroeléctricas con capacidad > 30 MW.
    (
        "ordinario",
        "hidroelectrico",
        2023,
        2026,
        Decimal("0.3500"),
        Decimal("0.0300"),
        "Art. 240 par. 5 ET",
        "Sobretasa 3% para generadoras hidroeléctricas con capacidad instalada > 30 MW (2023-2026).",
    ),
    # ── ESAL — entidades sin ánimo de lucro 20% (Art. 19 ET) ──
    (
        "esal",
        "general",
        2017,
        None,
        Decimal("0.2000"),
        Decimal("0"),
        "Art. 19 ET",
        "Tarifa 20% para contribuyentes del régimen tributario especial (ESAL). Vigente desde reforma 2017 (Ley 1819/2016).",
    ),
    # ── Zona franca — Art. 240-1 ET — 20% ──
    (
        "zona_franca",
        "general",
        2017,
        None,
        Decimal("0.2000"),
        Decimal("0"),
        "Art. 240-1 ET",
        "Tarifa 20% para usuarios de zonas francas y régimen simple de tributación (RST).",
    ),
]


def main() -> None:
    db = SessionLocal()
    try:
        inserted = 0
        for (
            regimen,
            actividad,
            year_from,
            year_to,
            tarifa_base,
            sobretasa,
            base_legal,
            notas,
        ) in ROWS:
            row = upsert_tarifa_renta(
                db,
                regimen=regimen,
                actividad=actividad,
                tarifa_base=tarifa_base,
                sobretasa=sobretasa,
                year_from=year_from,
                year_to=year_to,
                base_legal=base_legal,
                notas=notas,
            )
            action = "inserted" if row.id else "upserted"
            logger.info(
                "seed_tarifas_renta: %s (%s, %s, %d) → base=%.4f sobretasa=%.4f efectiva=%.4f [%s]",
                action,
                regimen,
                actividad,
                year_from,
                float(tarifa_base),
                float(sobretasa),
                float(tarifa_base + sobretasa),
                base_legal,
            )
            inserted += 1
        logger.info("seed_tarifas_renta: done — %d rows upserted.", inserted)
    except Exception:
        logger.exception("seed_tarifas_renta: failed")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
