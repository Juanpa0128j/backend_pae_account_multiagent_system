"""Unit tests for app/agents/contador_puc_corrector.py."""

from __future__ import annotations

import pytest

from app.agents.contador_puc_corrector import (
    correct_5195_fallback,
    correct_asiento_lines,
    correct_ce_220505_credit,
    correct_class_only_codes,
    correct_contador_output,
    correct_cross_class_codes,
)

# ---------------------------------------------------------------------------
# correct_5195_fallback — keyword-driven specialization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "descripcion,expected",
    [
        ("Pago de salarios enero", "510505"),
        ("Aporte a EPS empleado", "510527"),
        ("Aportes a ARL trabajadores", "510530"),
        ("Servicio público de energía", "513540"),
        ("Pago internet oficina", "513540"),
        ("Servicio de agua", "513540"),
        ("Combustible vehículo gerencia", "513025"),
        ("Honorarios contador", "511505"),
        ("Asesoría jurídica anual", "511515"),
        ("Mantenimiento equipos", "514505"),
        ("Servicio de aseo y limpieza", "514515"),
        ("Vigilancia mensual", "514525"),
        ("Compra de papelería oficina", "519525"),
        ("Licencia de software", "519525"),
        ("Hosting del dominio", "519525"),
        ("ICA enero", "521505"),
        ("Publicidad redes sociales", "523510"),
        ("4x1000 transferencia", "529505"),
        ("Comisión banco", "530525"),
        ("Cuotas afiliación cámara comercio", "519520"),
    ],
)
def test_5195_keyword_specialization(descripcion: str, expected: str):
    line = {
        "cuenta_puc": "5195",
        "tipo_movimiento": "debito",
        "descripcion": descripcion,
    }
    out = correct_5195_fallback(dict(line))
    assert out["cuenta_puc"] == expected, f"{descripcion!r} -> {out['cuenta_puc']}"


def test_5195_no_keyword_match_unchanged():
    line = {
        "cuenta_puc": "5195",
        "tipo_movimiento": "debito",
        "descripcion": "Concepto totalmente desconocido xyz",
    }
    out = correct_5195_fallback(dict(line))
    assert out["cuenta_puc"] == "5195"


def test_5195_credit_line_unchanged():
    line = {
        "cuenta_puc": "5195",
        "tipo_movimiento": "credito",
        "descripcion": "Pago salarios",
    }
    out = correct_5195_fallback(dict(line))
    assert out["cuenta_puc"] == "5195"


def test_non_5195_line_unchanged():
    line = {
        "cuenta_puc": "510505",
        "tipo_movimiento": "debito",
        "descripcion": "Sueldo",
    }
    out = correct_5195_fallback(dict(line))
    assert out["cuenta_puc"] == "510505"


# ---------------------------------------------------------------------------
# correct_ce_220505_credit
# ---------------------------------------------------------------------------


def test_ce_220505_credit_swapped_to_banco():
    line = {
        "cuenta_puc": "220505",
        "tipo_movimiento": "credito",
        "descripcion": "Pago factura proveedor",
    }
    out = correct_ce_220505_credit(dict(line), "comprobante_egreso")
    assert out["cuenta_puc"] == "111005"


def test_extracto_220505_credit_swapped_to_banco():
    """Bank statements have the same constraint as CEs — 220505 cred is a
    factura_compra leak that must become 111005 (banco)."""
    line = {
        "cuenta_puc": "220505",
        "tipo_movimiento": "credito",
        "descripcion": "PAGO DE PROV ALMACENES EXITO",
    }
    out = correct_ce_220505_credit(dict(line), "extracto_bancario")
    assert out["cuenta_puc"] == "111005"


def test_conciliacion_220505_credit_swapped_to_banco():
    line = {
        "cuenta_puc": "220505",
        "tipo_movimiento": "credito",
        "descripcion": "Ajuste conciliación banco",
    }
    out = correct_ce_220505_credit(dict(line), "conciliacion_bancaria")
    assert out["cuenta_puc"] == "111005"


def test_220505_credit_in_factura_compra_unchanged():
    line = {
        "cuenta_puc": "220505",
        "tipo_movimiento": "credito",
        "descripcion": "Causación factura compra",
    }
    out = correct_ce_220505_credit(dict(line), "factura_compra")
    assert out["cuenta_puc"] == "220505"


def test_ce_220505_debit_unchanged():
    line = {
        "cuenta_puc": "220505",
        "tipo_movimiento": "debito",
        "descripcion": "Anula CxP por pago CE",
    }
    out = correct_ce_220505_credit(dict(line), "comprobante_egreso")
    assert out["cuenta_puc"] == "220505"


def test_ce_other_credit_unchanged():
    line = {
        "cuenta_puc": "111005",
        "tipo_movimiento": "credito",
        "descripcion": "Banco salida",
    }
    out = correct_ce_220505_credit(dict(line), "comprobante_egreso")
    assert out["cuenta_puc"] == "111005"


# ---------------------------------------------------------------------------
# correct_class_only_codes
# ---------------------------------------------------------------------------


def test_class_only_4digit_specialized_via_keyword():
    line = {
        "cuenta_puc": "5135",
        "tipo_movimiento": "debito",
        "descripcion": "Pago servicio internet oficina",
    }
    out = correct_class_only_codes(dict(line))
    assert out["cuenta_puc"] == "513540"


def test_class_only_no_keyword_match_unchanged():
    line = {
        "cuenta_puc": "5135",
        "tipo_movimiento": "debito",
        "descripcion": "Concepto ambiguo",
    }
    out = correct_class_only_codes(dict(line))
    assert out["cuenta_puc"] == "5135"


def test_6digit_code_unchanged():
    line = {
        "cuenta_puc": "513540",
        "tipo_movimiento": "debito",
        "descripcion": "Internet",
    }
    out = correct_class_only_codes(dict(line))
    assert out["cuenta_puc"] == "513540"


def test_4digit_class_1xxx_unchanged():
    """Class 1 (assets) commonly uses 4-digit parents; do not touch."""
    line = {
        "cuenta_puc": "1110",
        "tipo_movimiento": "debito",
        "descripcion": "Bancos",
    }
    out = correct_class_only_codes(dict(line))
    assert out["cuenta_puc"] == "1110"


def test_4digit_class_5_with_mismatched_keyword_class_unchanged():
    """If a class-5 line has a keyword that maps to class 1 (e.g. 'inventario'),
    the corrector must NOT cross classes — leave the code alone.
    """
    line = {
        "cuenta_puc": "5135",
        "tipo_movimiento": "debito",
        "descripcion": "Inventario para stock",
    }
    out = correct_class_only_codes(dict(line))
    assert out["cuenta_puc"] == "5135"


# ---------------------------------------------------------------------------
# correct_asiento_lines / correct_contador_output
# ---------------------------------------------------------------------------


def test_correct_asiento_lines_chains_all_correctors():
    lines = [
        {
            "cuenta_puc": "5195",
            "tipo_movimiento": "debito",
            "descripcion": "Sueldo enero",
        },
        {"cuenta_puc": "220505", "tipo_movimiento": "credito", "descripcion": "Pago"},
        {
            "cuenta_puc": "5135",
            "tipo_movimiento": "debito",
            "descripcion": "Energía oficina",
        },
    ]
    out = correct_asiento_lines(lines, doc_subtype="comprobante_egreso")
    assert out[0]["cuenta_puc"] == "510505"
    assert out[1]["cuenta_puc"] == "111005"
    assert out[2]["cuenta_puc"] == "513540"


def test_correct_contador_output_flat_asientos_shape():
    output = {
        "tipo_documento": "comprobante_egreso",
        "asientos": [
            {
                "cuenta_puc": "5195",
                "tipo_movimiento": "debito",
                "descripcion": "Honorarios",
            },
            {
                "cuenta_puc": "220505",
                "tipo_movimiento": "credito",
                "descripcion": "Banco",
            },
        ],
    }
    out = correct_contador_output(output, doc_subtype="comprobante_egreso")
    assert out["asientos"][0]["cuenta_puc"] == "511505"
    assert out["asientos"][1]["cuenta_puc"] == "111005"


def test_correct_contador_output_legacy_lineas_shape():
    output = {
        "asientos": [
            {
                "lineas": [
                    {
                        "cuenta_puc": "5195",
                        "tipo_movimiento": "debito",
                        "descripcion": "Internet",
                    },
                ]
            },
        ]
    }
    out = correct_contador_output(output, doc_subtype="factura_compra")
    assert out["asientos"][0]["lineas"][0]["cuenta_puc"] == "513540"


def test_correct_contador_output_non_dict_passes_through():
    assert correct_contador_output("not a dict") == "not a dict"
    assert correct_contador_output(None) is None


def test_doc_subtype_default_factura_compra_keeps_220505():
    """Without doc_subtype hint, 220505 cred should NOT be swapped — only CEs."""
    output = {
        "asientos": [
            {
                "cuenta_puc": "220505",
                "tipo_movimiento": "credito",
                "descripcion": "CxP",
            },
        ]
    }
    out = correct_contador_output(output)
    assert out["asientos"][0]["cuenta_puc"] == "220505"


# ---------------------------------------------------------------------------
# correct_cross_class_codes — fix LLM mistakes that put a known account
# in the wrong class (e.g. 5110 + "Bancos" -> 111005)
# ---------------------------------------------------------------------------


def test_cross_class_swap_5110_banco_to_111005():
    line = {"cuenta_puc": "5110", "descripcion": "Movimiento en bancos"}
    out = correct_cross_class_codes(line)
    assert out["cuenta_puc"] == "111005"


def test_cross_class_swap_5305_interes_to_530520():
    line = {"cuenta_puc": "5305", "descripcion": "Intereses moratorios"}
    out = correct_cross_class_codes(line)
    assert out["cuenta_puc"] == "530520"


def test_cross_class_swap_5135_agua_to_513540():
    line = {"cuenta_puc": "5135", "descripcion": "Pago agua acueducto"}
    out = correct_cross_class_codes(line)
    assert out["cuenta_puc"] == "513540"


def test_cross_class_no_swap_unknown_combo():
    line = {"cuenta_puc": "5110", "descripcion": "Algo sin keyword"}
    out = correct_cross_class_codes(line)
    assert out["cuenta_puc"] == "5110"


def test_cross_class_no_swap_unknown_cuenta():
    line = {"cuenta_puc": "9999", "descripcion": "Bancos"}
    out = correct_cross_class_codes(line)
    assert out["cuenta_puc"] == "9999"


def test_correct_asiento_lines_runs_cross_class_before_class_only():
    """Cross-class corrector should run before same-class corrector so the
    5110 -> 111005 swap actually applies (the same-class corrector's
    startswith() guard would block it otherwise).
    """
    lines = [
        {"cuenta_puc": "5110", "descripcion": "Bancos", "tipo_movimiento": "debito"}
    ]
    out = correct_asiento_lines(lines)
    assert out[0]["cuenta_puc"] == "111005"
