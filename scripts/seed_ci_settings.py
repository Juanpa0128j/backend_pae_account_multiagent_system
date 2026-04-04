"""Seed company settings for CI integration tests.

Creates a CompanySettings row for NIT 800999888 (used in test fixtures)
if it doesn't already exist.
"""

import os
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def seed():
    with Session() as db:
        existing = db.execute(
            text("SELECT nit FROM company_settings WHERE nit = '800999888'")
        ).fetchone()

        if existing:
            print("company_settings for 800999888 already exists — skipping.")
            return

        db.execute(
            text("""
                INSERT INTO company_settings (
                    nit, nombre, ciudad, codigo_ciiu, iva_responsable,
                    tasa_retefuente_servicios, tasa_retefuente_bienes,
                    tasa_retefuente_arrendamiento, tasa_reteica,
                    tasa_iva_general, tasa_ica, tasa_renta
                ) VALUES (
                    '800999888', 'Empresa CI Test', 'Bogota', '6920', true,
                    0.110000, 0.030000, 0.035000, 0.006900,
                    0.190000, 0.006900, 0.350000
                )
            """)
        )
        db.commit()
        print("Seeded company_settings for NIT 800999888.")


if __name__ == "__main__":
    seed()
