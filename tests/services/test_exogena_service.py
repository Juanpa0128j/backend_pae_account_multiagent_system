"""
Unit tests for exogena_service.

DB queries are mocked. Tests cover:
  - DIAN normalization helpers (NIT, nombre)
  - validate_and_normalize_tercero
  - generate_formato_1001 output structure and concept mapping
  - generate_formato_2276 output structure
  - Validation error flagging and submission_ready flag
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.exogena_service import (
    generate_formato_1001,
    generate_formato_1007,
    generate_formato_1008,
    generate_formato_1009,
    generate_formato_2276,
    nit_dv,
    normalize_nit_dian,
    normalize_nombre_dian,
    validate_and_normalize_tercero,
)

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


class TestNormalizeNitDian:
    def test_strips_dots_and_dashes(self):
        assert normalize_nit_dian("900.123.456-7") == "9001234567"

    def test_digits_only_unchanged(self):
        assert normalize_nit_dian("900123456") == "900123456"

    def test_empty_string(self):
        assert normalize_nit_dian("") == ""

    def test_spaces_stripped(self):
        assert normalize_nit_dian("900 123 456") == "900123456"


class TestNormalizeNombreDian:
    def test_uppercase(self):
        assert normalize_nombre_dian("empresa demo") == "EMPRESA DEMO"

    def test_accents_removed(self):
        assert normalize_nombre_dian("García López") == "GARCIA LOPEZ"

    def test_ene_converted(self):
        assert normalize_nombre_dian("Año Nuevo") == "ANO NUEVO"

    def test_special_chars_stripped(self):
        result = normalize_nombre_dian("Empresa @#! S.A.S.")
        assert "@" not in result
        assert "#" not in result

    def test_empty_string(self):
        assert normalize_nombre_dian("") == ""


class TestValidateAndNormalizeTercero:
    def test_valid_tercero(self):
        result = validate_and_normalize_tercero("900.123.456", "Empresa Demo SAS")
        assert result["nit"] == "900123456"
        assert result["nombre"] == "EMPRESA DEMO SAS"
        assert result["submission_ready"] is True
        assert result["errors"] == []

    def test_empty_nit_flags_error(self):
        result = validate_and_normalize_tercero("", "Empresa SAS")
        assert result["submission_ready"] is False
        assert len(result["errors"]) > 0

    def test_empty_nombre_flags_error(self):
        result = validate_and_normalize_tercero("900123456", None)
        assert result["submission_ready"] is False
        assert len(result["errors"]) > 0

    def test_invalid_ciudad_codigo(self):
        result = validate_and_normalize_tercero("900123456", "Empresa SAS", "ABC")
        assert result["submission_ready"] is False
        assert any("municipio" in e for e in result["errors"])

    def test_valid_ciudad_codigo(self):
        result = validate_and_normalize_tercero("900123456", "Empresa SAS", "05001")
        assert result["submission_ready"] is True
        assert result["ciudad_codigo"] == "05001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    s = MagicMock()
    s.nit = "900123456"
    s.nombre = "EMPRESA DEMO SAS"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _mock_db(settings):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = settings
    return db


def _make_db_row(**kwargs):
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    row._mapping = kwargs
    return row


# ---------------------------------------------------------------------------
# Formato 1001
# ---------------------------------------------------------------------------


class TestFormato1001:
    @patch("app.services.exogena_service.sql_text")
    def test_returns_rows(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="511505",
            total_pagos=5_000_000,
            total_retefuente=200_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_1001(db, "900123456", 2025)

        assert len(rows) == 1
        assert rows[0]["formato"] == "1001"
        assert rows[0]["year"] == 2025
        assert rows[0]["tercero_nit"] == "800111222"
        assert rows[0]["concepto_dian"] == "5001"  # 511505 → servicios
        assert rows[0]["total_pagos"] == 5_000_000
        assert rows[0]["total_retefuente"] == 200_000

    @patch("app.services.exogena_service.sql_text")
    def test_nit_normalized(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="800.111.222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="6135",
            total_pagos=1_000_000,
            total_retefuente=25_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_1001(db, "900123456", 2025)
        assert rows[0]["tercero_nit"] == "800111222"

    @patch("app.services.exogena_service.sql_text")
    def test_unknown_tercero_not_submission_ready(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="111222333",
            tercero_nombre=None,
            cuenta_puc="5110",
            total_pagos=500_000,
            total_retefuente=20_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_1001(db, "900123456", 2025)
        assert rows[0]["submission_ready"] is False
        assert len(rows[0]["validation_errors"]) > 0

    @patch("app.services.exogena_service.sql_text")
    def test_concepto_compras_6xxx(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="6135",
            total_pagos=2_000_000,
            total_retefuente=50_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_1001(db, "900123456", 2025)
        assert rows[0]["concepto_dian"] == "5002"

    @patch("app.services.exogena_service.sql_text")
    def test_concepto_arrendamiento_53xxx(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="5305",
            total_pagos=1_000_000,
            total_retefuente=35_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_1001(db, "900123456", 2025)
        assert rows[0]["concepto_dian"] == "5003"

    def test_missing_company_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="CompanySettings not found"):
            generate_formato_1001(db, "000000000", 2025)


# ---------------------------------------------------------------------------
# Formato 2276
# ---------------------------------------------------------------------------


class TestFormato2276:
    @patch("app.services.exogena_service.sql_text")
    def test_returns_rows(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="900888777",
            tercero_nombre="CLIENTE ABC SAS",
            total_ingresos=10_000_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_2276(db, "900123456", 2025)

        assert len(rows) == 1
        assert rows[0]["formato"] == "2276"
        assert rows[0]["pagador_nit"] == "900888777"
        assert rows[0]["total_ingresos"] == 10_000_000

    @patch("app.services.exogena_service.sql_text")
    def test_nombre_normalized(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="900888777",
            tercero_nombre="García & Asociados Ltda.",
            total_ingresos=5_000_000,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_2276(db, "900123456", 2025)
        nombre = rows[0]["pagador_nombre"]
        assert nombre == nombre.upper()
        assert "Á" not in nombre
        assert "á" not in nombre

    @patch("app.services.exogena_service.sql_text")
    def test_amounts_are_integers(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)

        fake_row = _make_db_row(
            tercero_nit="900888777",
            tercero_nombre="CLIENTE SAS",
            total_ingresos=1_234_567.89,
        )
        db.execute.return_value.fetchall.return_value = [fake_row]

        rows = generate_formato_2276(db, "900123456", 2025)
        assert isinstance(rows[0]["total_ingresos"], int)

    def test_missing_company_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="CompanySettings not found"):
            generate_formato_2276(db, "000000000", 2025)

    @patch("app.services.exogena_service.sql_text")
    def test_empty_when_no_ingresos(self, mock_sql_text):
        settings = _make_settings()
        db = _mock_db(settings)
        db.execute.return_value.fetchall.return_value = []

        rows = generate_formato_2276(db, "900123456", 2025)
        assert rows == []


class TestFormato1001Aggregation:
    @patch("app.services.exogena_service.sql_text")
    def test_aggregates_multiple_pucs_same_concepto(self, mock_sql_text):
        """511505 and 511510 both map to concepto 5001 — should aggregate to one row."""
        settings = _make_settings()
        db = _mock_db(settings)

        # Same tercero, two different PUCs under servicios (51xx → 5001)
        row1 = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="511505",
            total_pagos=2_000_000,
            total_retefuente=80_000,
        )
        row2 = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="511510",
            total_pagos=1_500_000,
            total_retefuente=60_000,
        )
        db.execute.return_value.fetchall.return_value = [row1, row2]

        rows = generate_formato_1001(db, "900123456", 2025)

        # Must collapse to one row with summed totals
        assert len(rows) == 1
        assert rows[0]["concepto_dian"] == "5001"
        assert rows[0]["total_pagos"] == 3_500_000
        assert rows[0]["total_retefuente"] == 140_000

    @patch("app.services.exogena_service.sql_text")
    def test_different_conceptos_stay_separate(self, mock_sql_text):
        """Same tercero with both servicios (51xx) and compras (6xx) must remain 2 rows."""
        settings = _make_settings()
        db = _mock_db(settings)

        row1 = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="511505",
            total_pagos=1_000_000,
            total_retefuente=40_000,
        )
        row2 = _make_db_row(
            tercero_nit="800111222",
            tercero_nombre="PROVEEDOR SAS",
            cuenta_puc="6135",
            total_pagos=500_000,
            total_retefuente=12_500,
        )
        db.execute.return_value.fetchall.return_value = [row1, row2]

        rows = generate_formato_1001(db, "900123456", 2025)
        assert len(rows) == 2
        conceptos = {r["concepto_dian"] for r in rows}
        assert conceptos == {"5001", "5002"}


# ---------------------------------------------------------------------------
# DIAN check digit
# ---------------------------------------------------------------------------


class TestNitDv:
    def test_known_nit(self):
        # 900.123.456 → DV 8 (algoritmo Art. 555-1 ET)
        assert nit_dv("900123456") == "8"

    def test_empty_returns_blank(self):
        assert nit_dv("") == ""

    def test_strips_formatting(self):
        assert nit_dv("900.123.456") == "8"


# ---------------------------------------------------------------------------
# Formato 1007 — Ingresos recibidos
# ---------------------------------------------------------------------------


class TestPrefixRegexGuard:
    def test_rejects_unlisted_prefix(self):
        from app.services.exogena_service import _tercero_movimientos

        with pytest.raises(ValueError, match="prefix_regex no permitido"):
            _tercero_movimientos(MagicMock(), "900123456", 2025, "^99", False)


class TestFormato1007:
    @patch("app.services.exogena_service.sql_text")
    def test_ingresos_by_concepto(self, _mock_sql):
        db = _mock_db(_make_settings())
        db.execute.return_value.fetchall.return_value = [
            _make_db_row(
                tercero_nit="800111222",
                tercero_nombre="CLIENTE SAS",
                cuenta_puc="413505",
                total_debito=0,
                total_credito=3_000_000,
            ),
            _make_db_row(
                tercero_nit="800111222",
                tercero_nombre="CLIENTE SAS",
                cuenta_puc="421005",
                total_debito=0,
                total_credito=500_000,
            ),
        ]
        rows = generate_formato_1007(db, "900123456", 2025)
        by_concepto = {r["concepto"]: r for r in rows}
        assert by_concepto["4001"]["ingresos_brutos"] == 3_000_000  # 41 → ordinarias
        assert by_concepto["4002"]["ingresos_brutos"] == 500_000  # 42 → otros
        assert by_concepto["4001"]["formato"] == "1007"
        assert by_concepto["4001"]["razon_social"] == "CLIENTE SAS"
        assert by_concepto["4001"]["dv"] == "7"  # DV(800111222)


# ---------------------------------------------------------------------------
# Formato 1008 — CxC a 31-dic
# ---------------------------------------------------------------------------


class TestFormato1008:
    @patch("app.services.exogena_service.sql_text")
    def test_saldo_cuentas_por_cobrar(self, _mock_sql):
        db = _mock_db(_make_settings())
        db.execute.return_value.fetchall.return_value = [
            _make_db_row(
                tercero_nit="800111222",
                tercero_nombre="CLIENTE SAS",
                cuenta_puc="130505",
                total_debito=5_000_000,
                total_credito=3_000_000,
            )
        ]
        rows = generate_formato_1008(db, "900123456", 2025)
        assert len(rows) == 1
        assert rows[0]["formato"] == "1008"
        assert rows[0]["concepto"] == "1315"  # 1305 → clientes
        # CxC débito-normal: 5M - 3M = 2M
        assert rows[0]["saldo_cuentas_por_cobrar"] == 2_000_000

    @patch("app.services.exogena_service.sql_text")
    def test_zero_saldo_excluded(self, _mock_sql):
        db = _mock_db(_make_settings())
        db.execute.return_value.fetchall.return_value = [
            _make_db_row(
                tercero_nit="800111222",
                tercero_nombre="CLIENTE SAS",
                cuenta_puc="130505",
                total_debito=1_000_000,
                total_credito=1_000_000,
            )
        ]
        assert generate_formato_1008(db, "900123456", 2025) == []


# ---------------------------------------------------------------------------
# Formato 1009 — CxP a 31-dic
# ---------------------------------------------------------------------------


class TestFormato1009:
    @patch("app.services.exogena_service.sql_text")
    def test_saldo_cuentas_por_pagar(self, _mock_sql):
        db = _mock_db(_make_settings())
        db.execute.return_value.fetchall.return_value = [
            _make_db_row(
                tercero_nit="800111222",
                tercero_nombre="PROVEEDOR SAS",
                cuenta_puc="220505",
                total_debito=1_000_000,
                total_credito=6_000_000,
            )
        ]
        rows = generate_formato_1009(db, "900123456", 2025)
        assert len(rows) == 1
        assert rows[0]["formato"] == "1009"
        assert rows[0]["concepto"] == "2201"  # 2205 → proveedores
        # CxP crédito-normal: 6M - 1M = 5M
        assert rows[0]["saldo_cuentas_por_pagar"] == 5_000_000
