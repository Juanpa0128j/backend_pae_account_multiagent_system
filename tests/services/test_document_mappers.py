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
