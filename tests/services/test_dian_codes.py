"""Unit tests for app.services.dian_codes — address parser + municipio lookups."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.dian_codes import (
    NOMENCLATURA_DIAN,
    expand_address,
    lookup_municipio,
    lookup_municipio_by_name,
    normalize_address,
)


class TestNomenclaturaConstant:
    def test_has_core_codes(self):
        for code in ("AC", "AK", "AV", "CL", "CR", "DG", "TV", "KM", "AP", "OF"):
            assert code in NOMENCLATURA_DIAN

    def test_values_are_strings(self):
        for code, full in NOMENCLATURA_DIAN.items():
            assert isinstance(code, str)
            assert isinstance(full, str)
            assert len(full) > 0


class TestExpandAddress:
    def test_simple_calle(self):
        assert expand_address("CL 24 # 5-30") == "Calle 24 # 5-30"

    def test_carrera(self):
        assert expand_address("CR 7 # 80-50") == "Carrera 7 # 80-50"

    def test_avenida_carrera_full_form(self):
        # AK should expand to "Avenida carrera" not "Avenida"
        assert expand_address("AK 19 # 100-50").startswith("Avenida carrera")

    def test_case_insensitive(self):
        assert expand_address("cl 24") == "Calle 24"
        assert expand_address("Cl 24") == "Calle 24"

    def test_preserves_rest_of_string(self):
        out = expand_address("CL 24 # 5-30 AP 502 ED Torres")
        assert "Calle" in out
        assert "Apartamento" in out
        assert "Edificio" in out
        assert "502" in out

    def test_empty_input(self):
        assert expand_address("") == ""
        assert expand_address(None) is None  # type: ignore[arg-type]

    def test_unknown_token_passes_through(self):
        assert expand_address("XYZ 99") == "XYZ 99"


class TestNormalizeAddress:
    def test_calle(self):
        assert normalize_address("Calle 24 # 5-30") == "CL 24 # 5-30"

    def test_avenida_carrera_before_avenida(self):
        # "Avenida carrera" must resolve to AK, not "AV carrera"
        assert normalize_address("Avenida carrera 19 # 100-50") == "AK 19 # 100-50"

    def test_avenida_alone(self):
        assert normalize_address("Avenida 19 # 100-50") == "AV 19 # 100-50"

    def test_case_insensitive(self):
        assert normalize_address("calle 24") == "CL 24"
        assert normalize_address("CALLE 24") == "CL 24"

    def test_round_trip_idempotent(self):
        # expand then normalize should yield the original DIAN form
        original = "CL 24 # 5-30 AP 502"
        roundtrip = normalize_address(expand_address(original))
        assert roundtrip == original

    def test_empty_input(self):
        assert normalize_address("") == ""
        assert normalize_address(None) is None  # type: ignore[arg-type]


class TestLookupMunicipio:
    @staticmethod
    def _mock_db(row: dict | None):
        db = MagicMock()
        result = MagicMock()
        if row is None:
            result.fetchone.return_value = None
        else:
            mock_row = MagicMock()
            mock_row._mapping = row
            result.fetchone.return_value = mock_row
        db.execute.return_value = result
        return db

    def test_lookup_by_code_found(self):
        bogota = {
            "codigo": "11001",
            "nombre": "BOGOTÁ, D.C.",
            "departamento_codigo": "11",
            "departamento_nombre": "Bogotá D.C.",
        }
        db = self._mock_db(bogota)
        assert lookup_municipio(db, "11001") == bogota

    def test_lookup_by_code_not_found(self):
        db = self._mock_db(None)
        assert lookup_municipio(db, "99999") is None

    def test_lookup_by_code_pads_short_input(self):
        # "5001" should be normalised to "05001"
        medellin = {
            "codigo": "05001",
            "nombre": "MEDELLÍN",
            "departamento_codigo": "05",
            "departamento_nombre": "Antioquia",
        }
        db = self._mock_db(medellin)
        assert lookup_municipio(db, "5001") == medellin
        # Verify the query received the padded code
        call_args = db.execute.call_args
        assert call_args[0][1] == {"codigo": "05001"}

    def test_lookup_by_code_rejects_non_numeric(self):
        db = self._mock_db(None)
        assert lookup_municipio(db, "ABCDE") is None

    def test_lookup_by_code_rejects_none(self):
        db = MagicMock()
        assert lookup_municipio(db, None) is None  # type: ignore[arg-type]
        db.execute.assert_not_called()

    def test_lookup_by_name_found(self):
        cali = {
            "codigo": "76001",
            "nombre": "CALI",
            "departamento_codigo": "76",
            "departamento_nombre": "Valle del Cauca",
        }
        db = self._mock_db(cali)
        assert lookup_municipio_by_name(db, "Cali") == cali

    def test_lookup_by_name_strips_whitespace(self):
        santa_marta = {
            "codigo": "47001",
            "nombre": "SANTA MARTA",
            "departamento_codigo": "47",
            "departamento_nombre": "Magdalena",
        }
        db = self._mock_db(santa_marta)
        assert lookup_municipio_by_name(db, "  Santa Marta  ") == santa_marta
        call_args = db.execute.call_args
        assert call_args[0][1] == {"nombre": "Santa Marta"}

    def test_lookup_by_name_empty(self):
        db = MagicMock()
        assert lookup_municipio_by_name(db, "") is None
        assert lookup_municipio_by_name(db, None) is None  # type: ignore[arg-type]
        db.execute.assert_not_called()


@pytest.mark.parametrize(
    "abbreviation,expected",
    [
        ("CL", "Calle"),
        ("CR", "Carrera"),
        ("AV", "Avenida"),
        ("AK", "Avenida carrera"),
        ("AC", "Avenida calle"),
        ("DG", "Diagonal"),
        ("TV", "Transversal"),
        ("KM", "Kilómetro"),
        ("AP", "Apartamento"),
        ("OF", "Oficina"),
        ("ED", "Edificio"),
        ("PH", "Penthouse"),
    ],
)
def test_nomenclatura_canonical_pairs(abbreviation, expected):
    assert NOMENCLATURA_DIAN[abbreviation] == expected
