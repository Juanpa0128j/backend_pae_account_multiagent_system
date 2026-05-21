from decimal import Decimal

from app.services.document_mappers import (
    as_str,
    build_structured_transactions,
    infer_total_from_items,
    safe_datetime,
    safe_decimal,
    sanitize_for_json,
)


def test_as_str_returns_default_for_none() -> None:
    assert as_str(None) == ""
    assert as_str(None, default="fallback") == "fallback"


def test_as_str_converts_orm_value_to_string() -> None:
    # Simulate an ORM-like object with __str__
    class OrmValue:
        def __str__(self) -> str:
            return "orm_value"

    assert as_str(OrmValue()) == "orm_value"


def test_as_str_returns_plain_string() -> None:
    assert as_str("already string") == "already string"
    assert as_str(123) == "123"


def test_sanitize_decimal_to_string() -> None:
    assert sanitize_for_json(Decimal("10.5")) == "10.5"


def test_sanitize_datetime_to_isoformat() -> None:
    from datetime import datetime, timezone

    dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    assert sanitize_for_json(dt) == "2024-01-15T10:30:00+00:00"


def test_sanitize_nested_dict_and_list() -> None:
    from datetime import datetime, timezone

    payload = {
        "amount": Decimal("99.99"),
        "created_at": datetime(2024, 1, 15, tzinfo=timezone.utc),
        "items": [
            {"price": Decimal("10.00")},
            {"price": Decimal("20.00")},
        ],
    }
    expected = {
        "amount": "99.99",
        "created_at": "2024-01-15T00:00:00+00:00",
        "items": [
            {"price": "10.00"},
            {"price": "20.00"},
        ],
    }
    assert sanitize_for_json(payload) == expected


def test_sanitize_passes_through_primitives() -> None:
    assert sanitize_for_json(42) == 42
    assert sanitize_for_json("hello") == "hello"
    assert sanitize_for_json(None) is None
    assert sanitize_for_json(True) is True


def test_safe_decimal_parses_string() -> None:
    assert safe_decimal("123.45") == Decimal("123.45")


def test_safe_decimal_parses_int() -> None:
    assert safe_decimal(100) == Decimal("100")


def test_safe_decimal_returns_none_for_invalid() -> None:
    assert safe_decimal("not-a-number") is None


def test_safe_decimal_returns_none_for_none() -> None:
    assert safe_decimal(None) is None


def test_safe_datetime_parses_yyyy_mm_dd() -> None:
    from datetime import datetime, timezone

    result = safe_datetime("2024-01-15")
    assert result == datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)


def test_safe_datetime_parses_iso_datetime() -> None:
    from datetime import datetime, timezone

    result = safe_datetime("2024-01-15T10:30:00")
    assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


def test_safe_datetime_returns_datetime_unchanged() -> None:
    from datetime import datetime, timezone

    dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    assert safe_datetime(dt) is dt


def test_safe_datetime_returns_none_for_unparseable() -> None:
    assert safe_datetime("not-a-date") is None


def test_safe_datetime_parses_yyyy_mm_to_first_of_month() -> None:
    """PILA / monthly tax docs deliver ``periodo`` as YYYY-MM. The parser must
    treat that as the first day of that month at 00:00 UTC.
    """
    from datetime import datetime, timezone

    result = safe_datetime("2026-01")
    assert result == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_safe_datetime_normalizes_offset_to_utc() -> None:
    """Inputs with explicit non-UTC offsets (e.g. Colombian -05:00) must be
    converted to UTC so persisted rows never mix offsets.
    """
    from datetime import datetime, timezone

    # 2026-01-06 10:26:58 Bogotá == 2026-01-06 15:26:58 UTC
    result = safe_datetime("2026-01-06T10:26:58-05:00")
    assert result is not None
    assert result == datetime(2026, 1, 6, 15, 26, 58, tzinfo=timezone.utc)
    offset = result.utcoffset()
    assert offset is not None and offset.total_seconds() == 0


def test_safe_datetime_handles_z_suffix() -> None:
    from datetime import datetime, timezone

    result = safe_datetime("2026-01-06T10:26:58Z")
    assert result == datetime(2026, 1, 6, 10, 26, 58, tzinfo=timezone.utc)


def test_infer_total_from_line_totals() -> None:
    items = [
        {"valor_total_sin_impuesto": "100.00"},
        {"valor_total": "50.00"},
    ]
    assert infer_total_from_items(items) == Decimal("150.00")


def test_infer_total_from_unit_value_and_qty() -> None:
    items = [
        {"valor_unitario": "10.00", "cantidad": "3"},
    ]
    assert infer_total_from_items(items) == Decimal("30.00")


def test_infer_total_uses_unit_value_when_qty_missing() -> None:
    items = [
        {"valor_unitario": "25.00"},
    ]
    assert infer_total_from_items(items) == Decimal("25.00")


def test_infer_total_returns_none_for_empty_items() -> None:
    assert infer_total_from_items([]) is None
    assert infer_total_from_items(None) is None


def test_extracto_bancario_movements_to_tx_rows() -> None:
    interpreted = {
        "titular": {"nit": "123"},
        "receptor": {"nit": "456"},
        "periodo_inicio": "2024-01-01",
        "periodo_fin": "2024-01-31",
        "movements": [
            {
                "debito": "1000.00",
                "credito": "0",
                "descripcion": "Transferencia",
                "referencia": "REF-001",
                "fecha": "2024-01-15",
            },
            {
                "debito": "0",
                "credito": "500.00",
                "descripcion": "Deposito",
                "referencia": "",
                "fecha": "2024-01-20",
            },
        ],
    }
    txs = build_structured_transactions(interpreted, "extracto_bancario")
    assert len(txs) == 2
    assert txs[0]["total"] == "1000.00"
    assert txs[0]["concepto"] == "Transferencia (ref: REF-001)"
    assert txs[1]["total"] == "500.00"
    assert txs[1]["concepto"] == "Deposito"


def test_extracto_bancario_fallback_to_resumen() -> None:
    interpreted = {
        "titular": {"nit": "123"},
        "receptor": {"nit": "456"},
        "periodo_inicio": "2024-01-01",
        "periodo_fin": "2024-01-31",
        "resumen": {"total_debitos": "2000.00"},
    }
    txs = build_structured_transactions(interpreted, "extracto_bancario")
    assert len(txs) == 1
    assert txs[0]["total"] == "2000.00"
    assert txs[0]["concepto"] == "Extracto bancario"


def test_nomina_uses_total_devengado_as_gross_expense() -> None:
    """total passed to contador must be gross (devengado) not net, to avoid CR>DR imbalance."""
    interpreted = {
        "empresa": {"nit": "789"},
        "periodo_inicio": "2024-01-01",
        "periodo_fin": "2024-01-31",
        "total_devengado": "1166336.07",
        "total_neto_pagar": "1041789.07",
    }
    txs = build_structured_transactions(interpreted, "nomina")
    assert len(txs) == 1
    assert txs[0]["total"] == "1166336.07"
    assert "Periodo" in txs[0]["concepto"]


def test_nomina_falls_back_to_neto_when_devengado_missing() -> None:
    interpreted = {
        "empresa": {"nit": "789"},
        "periodo_inicio": "2024-01-01",
        "periodo_fin": "2024-01-31",
        "total_neto_pagar": "5000.00",
    }
    txs = build_structured_transactions(interpreted, "nomina")
    assert len(txs) == 1
    assert txs[0]["total"] == "5000.00"
    assert "Periodo" in txs[0]["concepto"]


def test_nomina_sums_empleados_when_total_missing() -> None:
    interpreted = {
        "empresa": {"nit": "789"},
        "periodo_inicio": "2024-01-01",
        "periodo_fin": "2024-01-31",
        "empleados": [
            {"neto_pagar": "2000.00"},
            {"neto_pagar": "3000.00"},
        ],
    }
    txs = build_structured_transactions(interpreted, "nomina")
    assert len(txs) == 1
    assert txs[0]["total"] == "5000.00"


def test_recibo_pago_impuesto_uses_total_pagado() -> None:
    interpreted = {
        "nit_declarante": "111",
        "fecha_pago": "2024-03-15",
        "total_pagado": "1500.00",
        "tipo_impuesto": "IVA",
        "periodo_gravable": "2024-01",
    }
    txs = build_structured_transactions(interpreted, "recibo_pago_impuesto")
    assert len(txs) == 1
    assert txs[0]["total"] == "1500.00"
    assert "IVA" in txs[0]["concepto"]
    assert "2024-01" in txs[0]["concepto"]


def test_liquidacion_cesantias_uses_consolidated_total_when_present() -> None:
    interpreted = {
        "empresa": {"nit": "900123456-7"},
        "fecha_pago": "2024-02-15",
        "numero_documento": "CES-001",
        "total_cesantias_liquidadas": "1250000.00",
        "total_intereses_cesantias": "150000.00",
        "total_prima_servicios": "0",
        "total_vacaciones": "0",
        "total_retenciones": "50000.00",
        "total_neto_pagar": "1350000.00",
        "empleados": [{"nombre": "Ana"}],
    }

    txs = build_structured_transactions(interpreted, "liquidacion_cesantias")

    assert len(txs) == 1
    assert txs[0]["total"] == "1250000.00"
    assert txs[0]["total_cesantias_liquidadas"] == "1250000.00"
    assert txs[0]["total_intereses_cesantias"] == "150000.00"
    assert txs[0]["fecha"] == "2024-02-15"
    assert txs[0]["nit_receptor"] == "900123456-7"


def test_liquidacion_cesantias_fallback_sums_empleados_when_total_missing() -> None:
    interpreted = {
        "empresa": {"nit": "900123456-7"},
        "fecha_liquidacion": "2024-02-10",
        "empleados": [
            {"valor_cesantias": "500000.00"},
            {"cesantias_liquidadas": "300000.00"},
            {"valor_cesantias": "200000.00"},
        ],
    }

    txs = build_structured_transactions(interpreted, "liquidacion_cesantias")

    assert len(txs) == 1
    assert txs[0]["total"] == "1000000.00"
    assert txs[0]["total_cesantias_liquidadas"] == "1000000.00"
    assert txs[0]["fecha"] == "2024-02-10"


def test_liquidacion_cesantias_preserves_asientos_documento() -> None:
    interpreted = {
        "empresa": {"nit": "900123456-7"},
        "total_cesantias_liquidadas": "1000.00",
        "asientos_documento": [
            {
                "codigo_cuenta": "510510",
                "concepto": "Cesantias",
                "debito": Decimal("1000.00"),
                "credito": Decimal("0"),
            },
            {
                "codigo_cuenta": "111005",
                "concepto": "Banco",
                "debito": Decimal("0"),
                "credito": Decimal("1000.00"),
            },
        ],
    }

    txs = build_structured_transactions(interpreted, "liquidacion_cesantias")

    assert len(txs) == 1
    assert "asientos_documento" in txs[0]
    assert len(txs[0]["asientos_documento"]) == 2
    assert txs[0]["asientos_documento"][0]["codigo_cuenta"] == "510510"
    assert txs[0]["asientos_documento"][0]["debito"] == "1000.00"


def test_generic_invoice_like_mapping() -> None:
    interpreted = {
        "emisor": {"nit": "111"},
        "receptor": {"nit": "222"},
        "fecha_emision": "2024-04-01",
        "totales": {"total_a_pagar": "1200.00"},
        "descripcion_general": "Factura de venta",
        "items": [
            {"valor_total": "600.00"},
            {"valor_total": "600.00"},
        ],
    }
    txs = build_structured_transactions(interpreted, "factura_venta")
    assert len(txs) == 1
    assert txs[0]["total"] == "1200.00"
    assert txs[0]["fecha"] == "2024-04-01"
    assert txs[0]["concepto"] == "Factura de venta"


def test_generic_infers_total_from_items_when_missing() -> None:
    interpreted = {
        "emisor": {"nit": "111"},
        "receptor": {"nit": "222"},
        "fecha": "2024-05-01",
        "concepto": "Servicios",
        "items": [
            {"valor_total": "300.00"},
            {"valor_total": "200.00"},
        ],
    }
    txs = build_structured_transactions(interpreted, "generic")
    assert len(txs) == 1
    assert txs[0]["total"] == "500.00"
    assert txs[0]["concepto"] == "Servicios"


def test_cuenta_cobro_branch_preserves_iva_zero_and_retencion_flag() -> None:
    """`cuenta_cobro` is issued by natural persons (no IVA, no invoice). The
    dedicated mapper branch must force `totales.total_iva=0`, surface
    `prestador.cedula`/`nit` as `nit_emisor`, and pass through the
    `aplicar_retencion=false` flag and any extracted retenciones so tributario
    can respect them downstream.
    """
    interpreted = {
        "numero": "2026-013",
        "fecha": "2026-01-24",
        "prestador": {"cedula": "1234567890", "nombre": "Vanessa Gomez"},
        "contratante": {"nit": "901016386", "razon_social": "Testing SAS"},
        "valor": 875000,
        "valor_neto": 875000,
        "concepto": "Outsourcing contable enero 2026",
        "retenciones": [],
        "informacion_adicional": {
            "aplicar_retencion": False,
            "motivo_no_retencion": "no aplicar retención según artículo 383 ET",
        },
    }

    txs = build_structured_transactions(interpreted, "cuenta_cobro")

    assert len(txs) == 1
    tx = txs[0]
    # nit_emisor falls back to prestador.cedula when prestador.nit is absent.
    assert tx["nit_emisor"] == "1234567890"
    assert tx["nit_receptor"] == "901016386"
    # CC by definition has no IVA — extractor must force total_iva=0.
    assert tx["totales"]["total_iva"] == "0"
    assert tx["totales"]["subtotal"] == "875000"
    assert tx["totales"]["total"] == "875000"
    # Retención opt-out flag must be preserved verbatim for tributario.
    assert tx["informacion_adicional"]["aplicar_retencion"] is False
    assert tx["retenciones_aplicadas"] == []
    # Concepto annotated with the document number (mapper concatenation).
    assert "2026-013" in tx["concepto"]
    assert "Outsourcing" in tx["concepto"]


class TestAuxiliarIVAMapper:
    def _two_account_payload(self) -> dict:
        from decimal import Decimal

        return {
            "entidad": {"nit": "900123456", "nombre": "Test SAS"},
            "periodo_inicio": "2026-02-01",
            "periodo_fin": "2026-02-28",
            "cuentas": [
                {
                    "codigo_cuenta": "24080101",
                    "nombre_cuenta": "IVA generado 19%",
                    "tipo_iva": "generado",
                    "saldo_inicial": Decimal("0"),
                    "movimientos": [{"fecha": "2026-02-15", "valor": "1000"}],
                    "total_debitos": Decimal("0"),
                    "total_creditos": Decimal("5000"),
                    "saldo_final": Decimal("5000"),
                },
                {
                    "codigo_cuenta": "24080201",
                    "nombre_cuenta": "IVA descontable 19%",
                    "tipo_iva": "descontable",
                    "saldo_inicial": Decimal("0"),
                    "movimientos": [],
                    "total_debitos": Decimal("3000"),
                    "total_creditos": Decimal("0"),
                    "saldo_final": Decimal("3000"),
                },
            ],
            "moneda": "COP",
        }

    def test_produces_one_tx_per_account(self) -> None:
        txs = build_structured_transactions(self._two_account_payload(), "auxiliar_iva")
        assert len(txs) == 2

    def test_fecha_is_periodo_fin(self) -> None:
        txs = build_structured_transactions(self._two_account_payload(), "auxiliar_iva")
        for tx in txs:
            assert tx["fecha"] == "2026-02-28"

    def test_total_uses_saldo_final_when_present(self) -> None:
        txs = build_structured_transactions(self._two_account_payload(), "auxiliar_iva")
        assert txs[0]["total"] == "5000"

    def test_total_falls_back_to_max_debito_credito(self) -> None:
        from decimal import Decimal

        payload = {
            "entidad": {"nit": "900123456"},
            "periodo_fin": "2026-02-28",
            "cuentas": [
                {
                    "codigo_cuenta": "24080101",
                    "nombre_cuenta": "IVA generado",
                    "tipo_iva": "generado",
                    "saldo_inicial": Decimal("0"),
                    "movimientos": [],
                    "total_debitos": Decimal("2000"),
                    "total_creditos": Decimal("7000"),
                    "saldo_final": Decimal("0"),
                }
            ],
        }
        txs = build_structured_transactions(payload, "auxiliar_iva")
        assert txs[0]["total"] == "7000"

    def test_empty_cuentas_returns_fallback(self) -> None:
        payload = {
            "entidad": {"nit": "900123456"},
            "periodo_fin": "2026-02-28",
            "cuentas": None,
        }
        txs = build_structured_transactions(payload, "auxiliar_iva")
        assert len(txs) == 1
        assert txs[0]["total"] == "0"
        assert txs[0]["concepto"] == "Auxiliar IVA"
        assert txs[0]["fecha"] == "2026-02-28"
        assert txs[0]["nit_emisor"] == "900123456"

    def test_items_capped_at_20_movimientos(self) -> None:
        from decimal import Decimal

        movimientos = [
            {"fecha": f"2026-02-{i:02d}", "valor": str(i)} for i in range(1, 26)
        ]
        payload = {
            "entidad": {"nit": "900123456"},
            "periodo_fin": "2026-02-28",
            "cuentas": [
                {
                    "codigo_cuenta": "24080101",
                    "nombre_cuenta": "IVA generado",
                    "tipo_iva": "generado",
                    "saldo_inicial": Decimal("0"),
                    "movimientos": movimientos,
                    "total_debitos": Decimal("0"),
                    "total_creditos": Decimal("100"),
                    "saldo_final": Decimal("100"),
                }
            ],
        }
        txs = build_structured_transactions(payload, "auxiliar_iva")
        assert len(txs[0]["items"]) == 20

    def test_concepto_uses_nombre_cuenta(self) -> None:
        txs = build_structured_transactions(self._two_account_payload(), "auxiliar_iva")
        assert txs[0]["concepto"] == "Auxiliar IVA IVA generado 19%"

    def test_nit_emisor_from_entidad(self) -> None:
        txs = build_structured_transactions(self._two_account_payload(), "auxiliar_iva")
        assert txs[0]["nit_emisor"] == "900123456"
        assert txs[0]["nit_receptor"] == ""

    def test_account_fields_present(self) -> None:
        txs = build_structured_transactions(self._two_account_payload(), "auxiliar_iva")
        tx = txs[0]
        assert tx["codigo_cuenta"] == "24080101"
        assert tx["tipo_iva"] == "generado"
        assert tx["total_debitos"] == "0"
        assert tx["total_creditos"] == "5000"
        assert tx["saldo_inicial"] == "0"
        assert tx["saldo_final"] == "5000"


class TestAutorretencionICAMapper:
    def _base_payload(self) -> dict:
        from decimal import Decimal

        return {
            "municipio": "Medellín",
            "departamento": "Antioquia",
            "anio": 2026,
            "periodicidad": "bimestral",
            "periodo_numero": 1,
            "nit_declarante": "900123456",
            "razon_social": "Test SAS",
            "detalle_autorretenciones": [
                {
                    "actividad_economica": "Comercio",
                    "codigo_ciiu": "4711",
                    "tarifa_retencion_por_mil": Decimal("5"),
                    "base_gravable": Decimal("10000000"),
                    "valor_autorretencion": Decimal("50000"),
                }
            ],
            "total_autorretenciones": Decimal("50000"),
            "sanciones": Decimal("0"),
            "intereses_mora": Decimal("0"),
            "total_a_pagar": Decimal("50000"),
            "tipo_declaracion": "inicial",
            "fecha_presentacion": "2026-03-10",
            "moneda": "COP",
        }

    def test_single_tx_produced(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "autorretencion_ica")
        assert len(txs) == 1

    def test_fecha_uses_fecha_presentacion_when_present(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "autorretencion_ica")
        assert txs[0]["fecha"] == "2026-03-10"

    def test_fecha_derives_from_period_when_no_presentacion(self) -> None:
        payload = self._base_payload()
        del payload["fecha_presentacion"]
        # bimestral periodo 1 → end of Feb = 2026-02-28
        txs = build_structured_transactions(payload, "autorretencion_ica")
        assert txs[0]["fecha"] == "2026-02-28"

    def test_total_uses_total_a_pagar(self) -> None:
        from decimal import Decimal

        payload = self._base_payload()
        payload["total_a_pagar"] = Decimal("75000")
        payload["total_autorretenciones"] = Decimal("50000")
        txs = build_structured_transactions(payload, "autorretencion_ica")
        assert txs[0]["total"] == "75000"

    def test_total_falls_back_to_autorretenciones(self) -> None:
        from decimal import Decimal

        payload = self._base_payload()
        payload["total_a_pagar"] = Decimal("0")
        payload["total_autorretenciones"] = Decimal("50000")
        txs = build_structured_transactions(payload, "autorretencion_ica")
        assert txs[0]["total"] == "50000"

    def test_total_falls_back_to_autorretenciones_when_none(self) -> None:
        from decimal import Decimal

        payload = self._base_payload()
        payload["total_a_pagar"] = None
        payload["total_autorretenciones"] = Decimal("50000")
        txs = build_structured_transactions(payload, "autorretencion_ica")
        assert txs[0]["total"] == "50000"

    def test_concepto_includes_municipio_and_period(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "autorretencion_ica")
        assert "Medellín" in txs[0]["concepto"]
        assert "1" in txs[0]["concepto"]
        assert "2026" in txs[0]["concepto"]

    def test_items_contain_detalle_autorretenciones(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "autorretencion_ica")
        assert len(txs[0]["items"]) == 1
        assert txs[0]["items"][0]["codigo_ciiu"] == "4711"

    def test_extra_fields_present(self) -> None:
        payload = self._base_payload()
        txs = build_structured_transactions(payload, "autorretencion_ica")
        tx = txs[0]
        assert tx["nit_emisor"] == "900123456"
        assert tx["nit_receptor"] == ""
        assert tx["municipio"] == "Medellín"
        assert tx["departamento"] == "Antioquia"
        assert tx["total_autorretenciones"] == "50000"
        assert tx["sanciones"] == "0"
        assert tx["intereses_mora"] == "0"


class TestDeclaracionICAMapper:
    def _base_payload(self) -> dict:
        from decimal import Decimal

        return {
            "municipio": "Bogotá",
            "departamento": "Cundinamarca",
            "anio": 2026,
            "periodicidad": "bimestral",
            "periodo_numero": 1,
            "nit_declarante": "900111222",
            "razon_social": "Test SA",
            "actividades_economicas": [
                {
                    "codigo_ciiu": "6201",
                    "descripcion": "Desarrollo software",
                    "tarifa_ica_por_mil": Decimal("5"),
                }
            ],
            "ingresos_brutos": Decimal("10000000"),
            "total_ingresos_gravables": Decimal("9500000"),
            "liquidacion": {
                "impuesto_ica": Decimal("47500"),
                "impuesto_avisos_tableros": Decimal("0"),
                "sobretasa_bomberil": Decimal("0"),
                "intereses_mora": Decimal("0"),
                "sanciones": Decimal("0"),
                "total_a_pagar": Decimal("47500"),
                "saldo_a_favor": Decimal("0"),
            },
            "tipo_declaracion": "inicial",
            "fecha_presentacion": "2026-03-15",
            "moneda": "COP",
        }

    def test_single_tx_produced(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "declaracion_ica")
        assert len(txs) == 1

    def test_fecha_uses_fecha_presentacion(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "declaracion_ica")
        assert txs[0]["fecha"] == "2026-03-15"

    def test_fecha_derives_from_period_when_no_presentacion(self) -> None:
        payload = self._base_payload()
        del payload["fecha_presentacion"]
        # bimestral periodo 1 → end of Feb = 2026-02-28
        txs = build_structured_transactions(payload, "declaracion_ica")
        assert txs[0]["fecha"] == "2026-02-28"

    def test_total_uses_liquidacion_total_a_pagar(self) -> None:
        from decimal import Decimal

        payload = self._base_payload()
        payload["liquidacion"]["total_a_pagar"] = Decimal("50000")
        payload["liquidacion"]["impuesto_ica"] = Decimal("47500")
        txs = build_structured_transactions(payload, "declaracion_ica")
        assert txs[0]["total"] == "50000"

    def test_total_falls_back_to_impuesto_ica(self) -> None:
        from decimal import Decimal

        payload = self._base_payload()
        payload["liquidacion"]["total_a_pagar"] = Decimal("0")
        payload["liquidacion"]["impuesto_ica"] = Decimal("47500")
        txs = build_structured_transactions(payload, "declaracion_ica")
        assert txs[0]["total"] == "47500"

    def test_total_falls_back_to_saldo_a_favor(self) -> None:
        from decimal import Decimal

        payload = self._base_payload()
        payload["liquidacion"]["total_a_pagar"] = Decimal("0")
        payload["liquidacion"]["impuesto_ica"] = Decimal("0")
        payload["liquidacion"]["saldo_a_favor"] = Decimal("5000")
        txs = build_structured_transactions(payload, "declaracion_ica")
        assert txs[0]["total"] == "5000"

    def test_concepto_includes_municipio(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "declaracion_ica")
        assert "Bogotá" in txs[0]["concepto"]
        assert "1" in txs[0]["concepto"]
        assert "2026" in txs[0]["concepto"]

    def test_items_contain_actividades_economicas(self) -> None:
        txs = build_structured_transactions(self._base_payload(), "declaracion_ica")
        assert len(txs[0]["items"]) == 1
        assert txs[0]["items"][0]["codigo_ciiu"] == "6201"

    def test_handles_missing_liquidacion(self) -> None:
        payload = self._base_payload()
        payload["liquidacion"] = None
        txs = build_structured_transactions(payload, "declaracion_ica")
        assert txs[0]["total"] == "0"


def test_cuenta_cobro_branch_uses_prestador_nit_when_present() -> None:
    """When the prestador exposes a NIT (e.g. PN registered as proveedor) the
    mapper prefers `prestador.nit` over `prestador.cedula`.
    """
    interpreted = {
        "fecha": "2026-02-01",
        "prestador": {
            "nit": "901999888",
            "cedula": "1234567890",
            "nombre": "Asesor PN",
        },
        "contratante": {"nit": "901016386"},
        "valor": 500000,
        "concepto": "Asesoría jurídica",
        "retenciones": [{"tipo": "retefuente", "valor": 50000}],
        "informacion_adicional": {},
    }

    txs = build_structured_transactions(interpreted, "cuenta_cobro")

    assert len(txs) == 1
    tx = txs[0]
    assert tx["nit_emisor"] == "901999888"
    assert tx["totales"]["total_iva"] == "0"
    # Extracted retenciones are preserved as-is for tributario consumption.
    assert tx["retenciones_aplicadas"] == [{"tipo": "retefuente", "valor": 50000}]
