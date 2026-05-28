"""Seed ReteICA municipal rates for major Colombian cities.

Sources:
- Bogotá: Acuerdo 065/2016, Acuerdo 756/2019
- Medellín: Acuerdo 67/2017
- Cali: Acuerdo 0373/2014
- Barranquilla: Decreto 0212/2018
- Bucaramanga: Acuerdo 017/2018
- Manizales: Acuerdo 938/2018
- Pereira: Acuerdo 11/2018
- Cartagena: Decreto 1746/2016
- Cúcuta: Acuerdo 047/2017
- Ibagué: Acuerdo 020/2016
- General fallback: 0.69% (national median)

Run: uv run python scripts/seed_reteica_tarifas.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.database import ReteicaTarifa
from app.core.logger import get_logger

logger = get_logger(__name__)

# (municipio, ciiu_seccion, tasa, fuente, base_minima_uvt)
# ciiu_seccion: A-U per DANE classification, or 'general' for city-wide default
# base_minima_uvt: municipal ReteICA threshold in UVT units.
#   Bogotá: 4 UVT (Acuerdo 756/2019), Medellín: 15 UVT (Acuerdo 67/2017),
#   Cali: 3 UVT (Acuerdo 0373/2014). Other cities: 4 UVT default (TBD — verify acuerdos).
#   Decreto 572 does NOT apply to ReteICA — each municipio sets its own base independently.
TARIFAS = [
    # ── Nacional fallback ──
    ("general", "general", 0.006900, "Mediana nacional ICA", 4),
    # ── Bogotá D.C. ──
    # Acuerdo 065/2016 + Acuerdo 756/2019; base_minima=4 UVT (Acuerdo 756/2019)
    ("bogota", "general", 0.009660, "Acuerdo 065 Bogotá 2016", 4),
    ("bogota", "A", 0.003000, "Acuerdo 065 Bogotá 2016 - Agricultura", 4),
    ("bogota", "B", 0.003000, "Acuerdo 065 Bogotá 2016 - Minas", 4),
    ("bogota", "C", 0.007200, "Acuerdo 065 Bogotá 2016 - Manufactura", 4),
    ("bogota", "D", 0.007200, "Acuerdo 065 Bogotá 2016 - Electricidad", 4),
    ("bogota", "E", 0.007200, "Acuerdo 065 Bogotá 2016 - Agua", 4),
    ("bogota", "F", 0.007200, "Acuerdo 065 Bogotá 2016 - Construcción", 4),
    ("bogota", "G", 0.007200, "Acuerdo 065 Bogotá 2016 - Comercio", 4),
    ("bogota", "H", 0.007200, "Acuerdo 065 Bogotá 2016 - Transporte", 4),
    ("bogota", "I", 0.009660, "Acuerdo 065 Bogotá 2016 - Alojamiento", 4),
    ("bogota", "J", 0.009660, "Acuerdo 065 Bogotá 2016 - Información/Tecnología", 4),
    ("bogota", "K", 0.009660, "Acuerdo 065 Bogotá 2016 - Financiero", 4),
    ("bogota", "L", 0.009660, "Acuerdo 065 Bogotá 2016 - Inmobiliario", 4),
    ("bogota", "M", 0.009660, "Acuerdo 065 Bogotá 2016 - Profesional/Científico", 4),
    ("bogota", "N", 0.009660, "Acuerdo 065 Bogotá 2016 - Administrativo", 4),
    ("bogota", "O", 0.009660, "Acuerdo 065 Bogotá 2016 - Administración pública", 4),
    ("bogota", "P", 0.009660, "Acuerdo 065 Bogotá 2016 - Educación", 4),
    ("bogota", "Q", 0.009660, "Acuerdo 065 Bogotá 2016 - Salud", 4),
    ("bogota", "R", 0.009660, "Acuerdo 065 Bogotá 2016 - Arte/Entretenimiento", 4),
    ("bogota", "S", 0.009660, "Acuerdo 065 Bogotá 2016 - Servicios varios", 4),
    ("bogota", "T", 0.009660, "Acuerdo 065 Bogotá 2016 - Hogares", 4),
    (
        "bogota",
        "U",
        0.009660,
        "Acuerdo 065 Bogotá 2016 - Organismos extraterritoriales",
        4,
    ),
    # ── Medellín ──
    # Acuerdo 67/2017; base_minima=15 UVT (Acuerdo 67/2017 Art. 8)
    ("medellin", "general", 0.010000, "Acuerdo 67 Medellín 2017", 15),
    ("medellin", "C", 0.005000, "Acuerdo 67 Medellín 2017 - Manufactura", 15),
    ("medellin", "G", 0.005000, "Acuerdo 67 Medellín 2017 - Comercio", 15),
    ("medellin", "J", 0.010000, "Acuerdo 67 Medellín 2017 - TIC", 15),
    ("medellin", "M", 0.010000, "Acuerdo 67 Medellín 2017 - Profesional", 15),
    # ── Cali ──
    # Acuerdo 0373/2014; base_minima=3 UVT (Acuerdo 0373/2014)
    ("cali", "general", 0.007000, "Acuerdo 0373 Cali 2014", 3),
    ("cali", "C", 0.005000, "Acuerdo 0373 Cali 2014 - Manufactura", 3),
    ("cali", "G", 0.005000, "Acuerdo 0373 Cali 2014 - Comercio", 3),
    ("cali", "M", 0.010000, "Acuerdo 0373 Cali 2014 - Profesional", 3),
    ("cali", "J", 0.010000, "Acuerdo 0373 Cali 2014 - TIC", 3),
    # ── Barranquilla ──
    # Decreto 0212/2018; base_minima=4 UVT (TBD — verify Decreto 0212)
    ("barranquilla", "general", 0.007000, "Decreto 0212 Barranquilla 2018", 4),  # TBD
    (
        "barranquilla",
        "C",
        0.004000,
        "Decreto 0212 Barranquilla 2018 - Manufactura",
        4,
    ),  # TBD
    (
        "barranquilla",
        "G",
        0.005000,
        "Decreto 0212 Barranquilla 2018 - Comercio",
        4,
    ),  # TBD
    (
        "barranquilla",
        "M",
        0.007000,
        "Decreto 0212 Barranquilla 2018 - Profesional",
        4,
    ),  # TBD
    # ── Bucaramanga ──
    # Acuerdo 017/2018; base_minima=4 UVT (TBD — verify Acuerdo 017)
    ("bucaramanga", "general", 0.007000, "Acuerdo 017 Bucaramanga 2018", 4),  # TBD
    (
        "bucaramanga",
        "C",
        0.005000,
        "Acuerdo 017 Bucaramanga 2018 - Manufactura",
        4,
    ),  # TBD
    ("bucaramanga", "G", 0.005000, "Acuerdo 017 Bucaramanga 2018 - Comercio", 4),  # TBD
    (
        "bucaramanga",
        "M",
        0.010000,
        "Acuerdo 017 Bucaramanga 2018 - Profesional",
        4,
    ),  # TBD
    # ── Manizales ──
    # Acuerdo 938/2018; base_minima=4 UVT (TBD — verify Acuerdo 938)
    ("manizales", "general", 0.006900, "Acuerdo 938 Manizales 2018", 4),  # TBD
    ("manizales", "C", 0.004000, "Acuerdo 938 Manizales 2018 - Manufactura", 4),  # TBD
    ("manizales", "G", 0.005000, "Acuerdo 938 Manizales 2018 - Comercio", 4),  # TBD
    # ── Pereira ──
    # Acuerdo 11/2018; base_minima=4 UVT (TBD — verify Acuerdo 11)
    ("pereira", "general", 0.006900, "Acuerdo 11 Pereira 2018", 4),  # TBD
    ("pereira", "C", 0.004000, "Acuerdo 11 Pereira 2018 - Manufactura", 4),  # TBD
    ("pereira", "G", 0.005000, "Acuerdo 11 Pereira 2018 - Comercio", 4),  # TBD
    # ── Cartagena ──
    # Decreto 1746/2016; base_minima=4 UVT (TBD — verify Decreto 1746)
    ("cartagena", "general", 0.007000, "Decreto 1746 Cartagena 2016", 4),  # TBD
    ("cartagena", "C", 0.004000, "Decreto 1746 Cartagena 2016 - Manufactura", 4),  # TBD
    ("cartagena", "G", 0.005000, "Decreto 1746 Cartagena 2016 - Comercio", 4),  # TBD
    (
        "cartagena",
        "I",
        0.007000,
        "Decreto 1746 Cartagena 2016 - Turismo/Hotelería",
        4,
    ),  # TBD
    # ── Cúcuta ──
    # Acuerdo 047/2017; base_minima=4 UVT (TBD — verify Acuerdo 047)
    ("cucuta", "general", 0.006900, "Acuerdo 047 Cúcuta 2017", 4),  # TBD
    ("cucuta", "C", 0.004000, "Acuerdo 047 Cúcuta 2017 - Manufactura", 4),  # TBD
    ("cucuta", "G", 0.005000, "Acuerdo 047 Cúcuta 2017 - Comercio", 4),  # TBD
    # ── Ibagué ──
    # Acuerdo 020/2016; base_minima=4 UVT (TBD — verify Acuerdo 020)
    ("ibague", "general", 0.006900, "Acuerdo 020 Ibagué 2016", 4),  # TBD
    ("ibague", "C", 0.004000, "Acuerdo 020 Ibagué 2016 - Manufactura", 4),  # TBD
    ("ibague", "G", 0.005000, "Acuerdo 020 Ibagué 2016 - Comercio", 4),  # TBD
]


def seed():
    db = SessionLocal()
    inserted = 0
    skipped = 0
    updated = 0
    try:
        for municipio, ciiu, tasa, fuente, base_uvt in TARIFAS:
            existing = (
                db.query(ReteicaTarifa)
                .filter(
                    ReteicaTarifa.municipio == municipio,
                    ReteicaTarifa.ciiu_seccion == ciiu,
                )
                .first()
            )
            if existing:
                # Update base_minima_uvt on existing rows (idempotent)
                from decimal import Decimal as _Dec

                if existing.base_minima_uvt != _Dec(str(base_uvt)):
                    existing.base_minima_uvt = base_uvt
                    updated += 1
                else:
                    skipped += 1
                continue
            db.add(
                ReteicaTarifa(
                    municipio=municipio,
                    ciiu_seccion=ciiu,
                    tasa=tasa,
                    fuente=fuente,
                    base_minima_uvt=base_uvt,
                )
            )
            inserted += 1
        db.commit()
        logger.info(
            f"ReteICA seed complete: {inserted} inserted, {updated} updated, {skipped} skipped."
        )
        print(f"Done: {inserted} inserted, {updated} updated, {skipped} skipped.")
    except Exception as e:
        db.rollback()
        logger.error(f"Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
