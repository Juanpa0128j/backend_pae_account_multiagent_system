from app.services.tributario_normalizer import normalize_tributario_output


def _state(contador_output=None, raw_transactions=None):
    return {
        "contador_output": contador_output or {},
        "raw_transactions": raw_transactions or [],
    }


def test_returns_dict():
    assert isinstance(normalize_tributario_output(_state(), {}), dict)


def test_empty_impuestos_sets_aplica_false():
    result = normalize_tributario_output(_state(), {"impuestos": []})
    assert result["aplica_impuestos"] is False


def test_non_list_impuestos_normalized_to_empty():
    result = normalize_tributario_output(_state(), {"impuestos": None})
    assert result["impuestos"] == []
    assert result["aplica_impuestos"] is False


def test_documento_referencia_fallback_to_contador():
    state = _state(contador_output={"descripcion_general": "Factura proveedor"})
    result = normalize_tributario_output(state, {})
    assert result["documento_referencia"] == "Factura proveedor"


def test_total_impuestos_summed():
    t = {"impuestos": [{"valor_impuesto": 100}, {"valor_impuesto": 50}]}
    result = normalize_tributario_output(_state(), t)
    from decimal import Decimal

    assert Decimal(result["total_impuestos"]) == Decimal("150")
