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
