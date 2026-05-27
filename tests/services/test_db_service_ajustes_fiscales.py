"""Unit tests for ajustes_fiscales helpers in db_service (in-memory SQLite)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.database import AjusteFiscal, CompanySettings
from app.services import db_service

NIT = "900000777"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    if not session.query(CompanySettings).filter_by(nit=NIT).first():
        session.add(
            CompanySettings(
                nit=NIT,
                nombre="Ajustes Test SA",
                tasa_renta=Decimal("0.35"),
                tasa_ica=Decimal("0.00690"),
                tasa_iva_general=Decimal("0.19"),
                tasa_reteica=Decimal("0.00414"),
            )
        )
        session.commit()
    yield session
    session.rollback()
    session.query(AjusteFiscal).delete()
    session.commit()
    session.close()


class TestUpsertAjusteFiscal:
    def test_insert_creates_new_row(self, db):
        row = db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ESF_ACTIVO",
            concepto="depreciacion_acelerada",
            valor_contable=Decimal("100000"),
            valor_fiscal=Decimal("120000"),
            tipo_diferencia="temporaria_imponible",
            descripcion="Art. 137 ET",
        )
        assert row.id is not None
        assert row.valor_fiscal == Decimal("120000")
        assert row.tipo_diferencia == "temporaria_imponible"

    def test_upsert_is_idempotent_on_unique_key(self, db):
        first = db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_GASTO",
            concepto="provisiones_no_deducibles",
            valor_contable=Decimal("50000"),
            valor_fiscal=Decimal("0"),
            tipo_diferencia="permanente",
        )
        second = db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_GASTO",
            concepto="provisiones_no_deducibles",
            valor_contable=Decimal("70000"),
            valor_fiscal=Decimal("0"),
            tipo_diferencia="permanente",
        )
        assert first.id == second.id
        assert second.valor_contable == Decimal("70000")
        # Only one row exists
        rows = (
            db.query(AjusteFiscal)
            .filter(
                AjusteFiscal.company_nit == NIT,
                AjusteFiscal.year == 2026,
                AjusteFiscal.seccion == "ERI_GASTO",
                AjusteFiscal.concepto == "provisiones_no_deducibles",
            )
            .all()
        )
        assert len(rows) == 1

    def test_different_year_creates_separate_row(self, db):
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2025,
            seccion="ESF_PASIVO",
            concepto="x",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("100"),
            tipo_diferencia="permanente",
        )
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ESF_PASIVO",
            concepto="x",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("200"),
            tipo_diferencia="permanente",
        )
        rows = db.query(AjusteFiscal).filter(AjusteFiscal.concepto == "x").all()
        assert len(rows) == 2

    def test_descripcion_preserved_when_omitted_on_update(self, db):
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_INGRESO",
            concepto="ing_no_grav",
            valor_contable=Decimal("100"),
            valor_fiscal=Decimal("80"),
            tipo_diferencia="permanente",
            descripcion="ingresos no constitutivos",
        )
        row = db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_INGRESO",
            concepto="ing_no_grav",
            valor_contable=Decimal("200"),
            valor_fiscal=Decimal("180"),
            tipo_diferencia="permanente",
            descripcion=None,
        )
        assert row.descripcion == "ingresos no constitutivos"


class TestListAjustesFiscales:
    def test_list_filters_by_nit_and_year(self, db):
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ESF_ACTIVO",
            concepto="a",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("1"),
            tipo_diferencia="permanente",
        )
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2025,
            seccion="ESF_ACTIVO",
            concepto="a",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("2"),
            tipo_diferencia="permanente",
        )
        rows = db_service.list_ajustes_fiscales(db, NIT, 2026)
        assert len(rows) == 1
        assert rows[0].year == 2026

    def test_list_filters_by_seccion(self, db):
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ESF_ACTIVO",
            concepto="a",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("1"),
            tipo_diferencia="permanente",
        )
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_GASTO",
            concepto="b",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("2"),
            tipo_diferencia="permanente",
        )
        active_only = db_service.list_ajustes_fiscales(
            db, NIT, 2026, seccion="ESF_ACTIVO"
        )
        assert len(active_only) == 1
        assert active_only[0].seccion == "ESF_ACTIVO"

    def test_list_empty_returns_empty_list(self, db):
        rows = db_service.list_ajustes_fiscales(db, NIT, 1999)
        assert rows == []

    def test_list_ordered_by_seccion_concepto(self, db):
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_GASTO",
            concepto="z",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("0"),
            tipo_diferencia="permanente",
        )
        db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ESF_ACTIVO",
            concepto="a",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("0"),
            tipo_diferencia="permanente",
        )
        rows = db_service.list_ajustes_fiscales(db, NIT, 2026)
        assert [r.seccion for r in rows] == ["ERI_GASTO", "ESF_ACTIVO"]


class TestDeleteAjusteFiscal:
    def test_delete_existing_returns_true(self, db):
        row = db_service.upsert_ajuste_fiscal(
            db,
            company_nit=NIT,
            year=2026,
            seccion="ERI_COSTO",
            concepto="del_me",
            valor_contable=Decimal("0"),
            valor_fiscal=Decimal("0"),
            tipo_diferencia="permanente",
        )
        assert db_service.delete_ajuste_fiscal(db, row.id) is True
        assert db.query(AjusteFiscal).filter(AjusteFiscal.id == row.id).first() is None

    def test_delete_missing_returns_false(self, db):
        assert (
            db_service.delete_ajuste_fiscal(db, "00000000-0000-0000-0000-000000000000")
            is False
        )
