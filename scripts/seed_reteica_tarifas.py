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

# (municipio, ciiu_seccion, tasa, fuente)
# ciiu_seccion: A-U per DANE classification, or 'general' for city-wide default
TARIFAS = [
    # ── Nacional fallback ──
    ("general", "general", 0.006900, "Mediana nacional ICA"),
    # ── Bogotá D.C. ──
    # Acuerdo 065/2016 + Acuerdo 756/2019
    ("bogota", "general", 0.009660, "Acuerdo 065 Bogotá 2016"),
    ("bogota", "A", 0.003000, "Acuerdo 065 Bogotá 2016 - Agricultura"),
    ("bogota", "B", 0.003000, "Acuerdo 065 Bogotá 2016 - Minas"),
    ("bogota", "C", 0.007200, "Acuerdo 065 Bogotá 2016 - Manufactura"),
    ("bogota", "D", 0.007200, "Acuerdo 065 Bogotá 2016 - Electricidad"),
    ("bogota", "E", 0.007200, "Acuerdo 065 Bogotá 2016 - Agua"),
    ("bogota", "F", 0.007200, "Acuerdo 065 Bogotá 2016 - Construcción"),
    ("bogota", "G", 0.007200, "Acuerdo 065 Bogotá 2016 - Comercio"),
    ("bogota", "H", 0.007200, "Acuerdo 065 Bogotá 2016 - Transporte"),
    ("bogota", "I", 0.009660, "Acuerdo 065 Bogotá 2016 - Alojamiento"),
    ("bogota", "J", 0.009660, "Acuerdo 065 Bogotá 2016 - Información/Tecnología"),
    ("bogota", "K", 0.009660, "Acuerdo 065 Bogotá 2016 - Financiero"),
    ("bogota", "L", 0.009660, "Acuerdo 065 Bogotá 2016 - Inmobiliario"),
    ("bogota", "M", 0.009660, "Acuerdo 065 Bogotá 2016 - Profesional/Científico"),
    ("bogota", "N", 0.009660, "Acuerdo 065 Bogotá 2016 - Administrativo"),
    ("bogota", "O", 0.009660, "Acuerdo 065 Bogotá 2016 - Administración pública"),
    ("bogota", "P", 0.009660, "Acuerdo 065 Bogotá 2016 - Educación"),
    ("bogota", "Q", 0.009660, "Acuerdo 065 Bogotá 2016 - Salud"),
    ("bogota", "R", 0.009660, "Acuerdo 065 Bogotá 2016 - Arte/Entretenimiento"),
    ("bogota", "S", 0.009660, "Acuerdo 065 Bogotá 2016 - Servicios varios"),
    ("bogota", "T", 0.009660, "Acuerdo 065 Bogotá 2016 - Hogares"),
    (
        "bogota",
        "U",
        0.009660,
        "Acuerdo 065 Bogotá 2016 - Organismos extraterritoriales",
    ),
    # ── Medellín ──
    # Acuerdo 67/2017
    ("medellin", "general", 0.010000, "Acuerdo 67 Medellín 2017"),
    ("medellin", "C", 0.005000, "Acuerdo 67 Medellín 2017 - Manufactura"),
    ("medellin", "G", 0.005000, "Acuerdo 67 Medellín 2017 - Comercio"),
    ("medellin", "J", 0.010000, "Acuerdo 67 Medellín 2017 - TIC"),
    ("medellin", "M", 0.010000, "Acuerdo 67 Medellín 2017 - Profesional"),
    # ── Cali ──
    # Acuerdo 0373/2014
    ("cali", "general", 0.007000, "Acuerdo 0373 Cali 2014"),
    ("cali", "C", 0.005000, "Acuerdo 0373 Cali 2014 - Manufactura"),
    ("cali", "G", 0.005000, "Acuerdo 0373 Cali 2014 - Comercio"),
    ("cali", "M", 0.010000, "Acuerdo 0373 Cali 2014 - Profesional"),
    ("cali", "J", 0.010000, "Acuerdo 0373 Cali 2014 - TIC"),
    # ── Barranquilla ──
    # Decreto 0212/2018
    ("barranquilla", "general", 0.007000, "Decreto 0212 Barranquilla 2018"),
    ("barranquilla", "C", 0.004000, "Decreto 0212 Barranquilla 2018 - Manufactura"),
    ("barranquilla", "G", 0.005000, "Decreto 0212 Barranquilla 2018 - Comercio"),
    ("barranquilla", "M", 0.007000, "Decreto 0212 Barranquilla 2018 - Profesional"),
    # ── Bucaramanga ──
    # Acuerdo 017/2018
    ("bucaramanga", "general", 0.007000, "Acuerdo 017 Bucaramanga 2018"),
    ("bucaramanga", "C", 0.005000, "Acuerdo 017 Bucaramanga 2018 - Manufactura"),
    ("bucaramanga", "G", 0.005000, "Acuerdo 017 Bucaramanga 2018 - Comercio"),
    ("bucaramanga", "M", 0.010000, "Acuerdo 017 Bucaramanga 2018 - Profesional"),
    # ── Manizales ──
    # Acuerdo 938/2018
    ("manizales", "general", 0.006900, "Acuerdo 938 Manizales 2018"),
    ("manizales", "C", 0.004000, "Acuerdo 938 Manizales 2018 - Manufactura"),
    ("manizales", "G", 0.005000, "Acuerdo 938 Manizales 2018 - Comercio"),
    # ── Pereira ──
    # Acuerdo 11/2018
    ("pereira", "general", 0.006900, "Acuerdo 11 Pereira 2018"),
    ("pereira", "C", 0.004000, "Acuerdo 11 Pereira 2018 - Manufactura"),
    ("pereira", "G", 0.005000, "Acuerdo 11 Pereira 2018 - Comercio"),
    # ── Cartagena ──
    # Decreto 1746/2016
    ("cartagena", "general", 0.007000, "Decreto 1746 Cartagena 2016"),
    ("cartagena", "C", 0.004000, "Decreto 1746 Cartagena 2016 - Manufactura"),
    ("cartagena", "G", 0.005000, "Decreto 1746 Cartagena 2016 - Comercio"),
    ("cartagena", "I", 0.007000, "Decreto 1746 Cartagena 2016 - Turismo/Hotelería"),
    # ── Cúcuta ──
    # Acuerdo 047/2017
    ("cucuta", "general", 0.006900, "Acuerdo 047 Cúcuta 2017"),
    ("cucuta", "C", 0.004000, "Acuerdo 047 Cúcuta 2017 - Manufactura"),
    ("cucuta", "G", 0.005000, "Acuerdo 047 Cúcuta 2017 - Comercio"),
    # ── Ibagué ──
    # Acuerdo 020/2016
    ("ibague", "general", 0.006900, "Acuerdo 020 Ibagué 2016"),
    ("ibague", "C", 0.004000, "Acuerdo 020 Ibagué 2016 - Manufactura"),
    ("ibague", "G", 0.005000, "Acuerdo 020 Ibagué 2016 - Comercio"),
]


def seed():
    db = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        for municipio, ciiu, tasa, fuente in TARIFAS:
            existing = (
                db.query(ReteicaTarifa)
                .filter(
                    ReteicaTarifa.municipio == municipio,
                    ReteicaTarifa.ciiu_seccion == ciiu,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue
            db.add(
                ReteicaTarifa(
                    municipio=municipio,
                    ciiu_seccion=ciiu,
                    tasa=tasa,
                    fuente=fuente,
                )
            )
            inserted += 1
        db.commit()
        logger.info(f"ReteICA seed complete: {inserted} inserted, {skipped} skipped.")
        print(f"Done: {inserted} inserted, {skipped} skipped.")
    except Exception as e:
        db.rollback()
        logger.error(f"Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
