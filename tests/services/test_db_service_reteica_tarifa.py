import pytest
from decimal import Decimal
from unittest.mock import MagicMock
from sqlalchemy.orm import Session
from app.models.database import ReteicaTarifa
from app.services import db_service


@pytest.fixture
def db():
    return MagicMock(spec=Session)


def _make_tarifa(
    id=1,
    municipio="bogota",
    ciiu_seccion="J",
    tasa=0.00966,
    fuente="Acuerdo 065",
    base_minima_uvt=4.0,
):
    row = MagicMock(spec=ReteicaTarifa)
    row.id = id
    row.municipio = municipio
    row.ciiu_seccion = ciiu_seccion
    row.tasa = Decimal(str(tasa))
    row.fuente = fuente
    row.base_minima_uvt = Decimal(str(base_minima_uvt))
    return row


class TestListReteicaTarifas:
    def test_returns_all_rows_when_no_filter(self, db):
        row = _make_tarifa()
        db.query.return_value.order_by.return_value.all.return_value = [row]
        result = db_service.list_reteica_tarifas(db)
        assert len(result) == 1
        assert result[0]["municipio"] == "bogota"

    def test_filters_by_municipio(self, db):
        row = _make_tarifa()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            row
        ]
        result = db_service.list_reteica_tarifas(db, municipio="bogota")
        assert result[0]["municipio"] == "bogota"

    def test_returns_empty_list_when_none(self, db):
        db.query.return_value.order_by.return_value.all.return_value = []
        result = db_service.list_reteica_tarifas(db)
        assert result == []


class TestUpsertReteicaTarifa:
    def test_inserts_new_row(self, db):
        db.query.return_value.filter.return_value.first.return_value = None
        db.refresh.side_effect = lambda r: None
        db_service.upsert_reteica_tarifa(
            db,
            municipio="medellin",
            ciiu_seccion="G",
            tasa=Decimal("0.005"),
            fuente="Acuerdo 023",
            base_minima_uvt=Decimal("15"),
        )
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_row(self, db):
        existing = _make_tarifa()
        db.query.return_value.filter.return_value.first.return_value = existing
        db_service.upsert_reteica_tarifa(
            db,
            municipio="bogota",
            ciiu_seccion="J",
            tasa=Decimal("0.01"),
            fuente="Acuerdo 099",
            base_minima_uvt=Decimal("4"),
        )
        assert existing.tasa == Decimal("0.01")
        assert existing.fuente == "Acuerdo 099"
        db.commit.assert_called_once()


class TestDeleteReteicaTarifa:
    def test_returns_true_when_found(self, db):
        row = _make_tarifa()
        db.query.return_value.filter.return_value.first.return_value = row
        result = db_service.delete_reteica_tarifa(db, row_id=1)
        assert result is True
        db.delete.assert_called_once_with(row)
        db.commit.assert_called_once()

    def test_returns_false_when_not_found(self, db):
        db.query.return_value.filter.return_value.first.return_value = None
        result = db_service.delete_reteica_tarifa(db, row_id=999)
        assert result is False
        db.delete.assert_not_called()


def test_reteica_schemas_importable():
    from app.models.schemas import ReteicaTarifaResponse, ReteicaTarifaUpsertRequest

    assert ReteicaTarifaResponse
    assert ReteicaTarifaUpsertRequest


def test_reteica_upsert_request_validates_ciiu():
    from app.models.schemas import ReteicaTarifaUpsertRequest
    import pytest

    with pytest.raises(Exception):
        ReteicaTarifaUpsertRequest(municipio="bogota", ciiu_seccion="Z", tasa=0.005)


def test_reteica_upsert_request_validates_tasa_max():
    from app.models.schemas import ReteicaTarifaUpsertRequest
    import pytest

    with pytest.raises(Exception):
        ReteicaTarifaUpsertRequest(municipio="bogota", ciiu_seccion="J", tasa=0.5)
