"""Tests for tipo_iva inference from contador journal entries."""

from __future__ import annotations

from app.services.tax_constants import (
    TIPO_IVA_EXPORTACION,
    TIPO_IVA_GRAVADO_19,
    TIPO_IVA_GRAVADO_5,
    infer_tipo_iva_from_journal,
)


def _line(cuenta: str, debito: float = 0.0, credito: float = 0.0) -> dict:
    return {"cuenta": cuenta, "debito": debito, "credito": credito}


def test_infer_gravado_19_when_240805_credited():
    entries = [
        _line("130505", debito=1_190_000),
        _line("4135", credito=1_000_000),
        _line("240805", credito=190_000),
    ]
    assert infer_tipo_iva_from_journal(entries) == TIPO_IVA_GRAVADO_19


def test_infer_gravado_5_when_240807_credited():
    entries = [
        _line("130505", debito=1_050_000),
        _line("4135", credito=1_000_000),
        _line("240807", credito=50_000),
    ]
    assert infer_tipo_iva_from_journal(entries) == TIPO_IVA_GRAVADO_5


def test_infer_exportacion_by_account_4175():
    entries = [
        _line("130505", debito=1_000_000),
        _line("4175", credito=1_000_000),
    ]
    assert infer_tipo_iva_from_journal(entries) == TIPO_IVA_EXPORTACION


def test_infer_exportacion_by_descripcion_keyword():
    entries = [
        _line("130505", debito=1_000_000),
        _line("4135", credito=1_000_000),
    ]
    out = infer_tipo_iva_from_journal(
        entries, descripcion="Venta de exportación a Ecuador"
    )
    assert out == TIPO_IVA_EXPORTACION


def test_infer_none_when_class4_credit_without_iva_signal():
    entries = [
        _line("130505", debito=1_000_000),
        _line("4135", credito=1_000_000),
    ]
    # Ambiguo: exento vs excluido — devolver None.
    assert infer_tipo_iva_from_journal(entries) is None


def test_infer_none_when_no_class4_credit():
    entries = [
        _line("5110", debito=500_000),
        _line("111005", credito=500_000),
    ]
    assert infer_tipo_iva_from_journal(entries) is None


def test_infer_none_for_empty_journal():
    assert infer_tipo_iva_from_journal([]) is None
    assert infer_tipo_iva_from_journal(None) is None


def test_infer_gravado_19_dominates_over_export_account():
    # If the asiento has 240805 (which is a hard IVA-generado signal),
    # treat it as gravado_19 even if 4175 also appears.
    entries = [
        _line("130505", debito=1_190_000),
        _line("4135", credito=1_000_000),
        _line("4175", credito=0),
        _line("240805", credito=190_000),
    ]
    assert infer_tipo_iva_from_journal(entries) == TIPO_IVA_GRAVADO_19


def test_infer_handles_string_amounts():
    entries = [
        {"cuenta": "4135", "credito": "1000000", "debito": "0"},
        {"cuenta": "240805", "credito": "190000", "debito": "0"},
    ]
    assert infer_tipo_iva_from_journal(entries) == TIPO_IVA_GRAVADO_19
