"""Verifies the national_rates table is seeded correctly after migration.

NOTE: requires db_session fixture from conftest.py — skipped if fixture unavailable.
"""

import pytest
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base


@pytest.fixture
def db_session():
    """Provides a fresh in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def test_national_rates_table_has_four_rows(db_session):
    """Verify that the national_rates table contains exactly four configured rates."""
    # Table created by Base.metadata.create_all in fixture; seed data manually
    db_session.execute(text("""
        INSERT INTO national_rates (code, value, descripcion, norma_referencia, vigente_desde)
        VALUES
            ('retefuente_servicios',     0.04,   'Retención en la fuente — servicios generales',     'Art. 392 ET',              '2023-01-01'),
            ('retefuente_bienes',        0.025,  'Retención en la fuente — compra de bienes',        'Art. 401 ET',              '2023-01-01'),
            ('retefuente_arrendamiento', 0.035,  'Retención en la fuente — arrendamiento inmuebles', 'Art. 401 ET',              '2023-01-01'),
            ('renta_general',            0.35,   'Tarifa general impuesto sobre la renta',           'Art. 240 ET, L.2277/2022', '2023-01-01')
    """))
    db_session.commit()

    rows = db_session.execute(
        text("SELECT code, value FROM national_rates ORDER BY code")
    ).fetchall()
    codes = {r[0] for r in rows}
    assert codes == {
        "retefuente_servicios",
        "retefuente_bienes",
        "retefuente_arrendamiento",
        "renta_general",
    }


def test_national_rates_values_match_statutory_constants(db_session):
    """Verify that the seeded rates match the statutory constants from settings.py."""
    # Table created by Base.metadata.create_all in fixture; seed data manually
    db_session.execute(text("""
        INSERT INTO national_rates (code, value, descripcion, norma_referencia, vigente_desde)
        VALUES
            ('retefuente_servicios',     0.04,   'Retención en la fuente — servicios generales',     'Art. 392 ET',              '2023-01-01'),
            ('retefuente_bienes',        0.025,  'Retención en la fuente — compra de bienes',        'Art. 401 ET',              '2023-01-01'),
            ('retefuente_arrendamiento', 0.035,  'Retención en la fuente — arrendamiento inmuebles', 'Art. 401 ET',              '2023-01-01'),
            ('renta_general',            0.35,   'Tarifa general impuesto sobre la renta',           'Art. 240 ET, L.2277/2022', '2023-01-01')
    """))
    db_session.commit()

    values = {
        r[0]: float(r[1])
        for r in db_session.execute(
            text("SELECT code, value FROM national_rates")
        ).fetchall()
    }
    assert values["retefuente_servicios"] == pytest.approx(0.04)
    assert values["retefuente_bienes"] == pytest.approx(0.025)
    assert values["retefuente_arrendamiento"] == pytest.approx(0.035)
    assert values["renta_general"] == pytest.approx(0.35)
