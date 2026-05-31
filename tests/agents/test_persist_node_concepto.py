"""Tests for concepto_retencion + tipo_persona inference helpers."""

from __future__ import annotations

from app.services.tax_constants import (
    TIPO_PERSONA_PJ,
    TIPO_PERSONA_PN,
    infer_concepto_retencion,
    infer_tipo_persona_from_nit,
    is_valid_aplica_a,
    is_valid_tipo_persona,
)

# --- tipo_persona inference ---


def test_nit_starting_9_is_pj():
    # Empresarial NIT example.
    assert infer_tipo_persona_from_nit("900123456-7") == TIPO_PERSONA_PJ


def test_nit_starting_8_is_pj():
    assert infer_tipo_persona_from_nit("800123456-1") == TIPO_PERSONA_PJ


def test_short_cedula_is_pn():
    assert infer_tipo_persona_from_nit("12345678") == TIPO_PERSONA_PN


def test_empty_nit_returns_none():
    assert infer_tipo_persona_from_nit("") is None
    assert infer_tipo_persona_from_nit(None) is None


def test_non_numeric_nit_returns_none():
    assert infer_tipo_persona_from_nit("abc-def") is None


# --- concepto inference ---


def test_concepto_5135_arrendamiento_pj():
    assert infer_concepto_retencion("513505", TIPO_PERSONA_PJ) == "arrendamiento_pj"


def test_concepto_511505_honorarios_pj():
    assert infer_concepto_retencion("511505", TIPO_PERSONA_PJ) == "honorarios_pj"


def test_concepto_511505_honorarios_pn():
    assert infer_concepto_retencion("511505", TIPO_PERSONA_PN) == "honorarios_pn"


def test_concepto_143505_compras_pj():
    assert infer_concepto_retencion("143505", TIPO_PERSONA_PJ) == "compras_pj"


def test_concepto_hidrocarburos_from_descripcion():
    assert (
        infer_concepto_retencion(
            "519595", None, descripcion="Compra de hidrocarburos refinados"
        )
        == "hidrocarburos"
    )


def test_concepto_pes_from_descripcion():
    assert (
        infer_concepto_retencion(
            "519595",
            None,
            descripcion="Servicio digital plataforma streaming",
        )
        == "pes_svcs_dig"
    )


def test_concepto_no_match_returns_none():
    assert infer_concepto_retencion("999999", TIPO_PERSONA_PJ) is None


def test_concepto_empty_puc_returns_none():
    assert infer_concepto_retencion(None, TIPO_PERSONA_PJ) is None


def test_concepto_pj_default_when_persona_none():
    # When tipo_persona is None, defaults to PJ resolution.
    assert infer_concepto_retencion("511505", None) == "honorarios_pj"


# --- validators ---


def test_is_valid_aplica_a():
    assert is_valid_aplica_a("PJ") is True
    assert is_valid_aplica_a("PN") is True
    assert is_valid_aplica_a("AMB") is True
    assert is_valid_aplica_a(None) is True
    assert is_valid_aplica_a("XX") is False


def test_is_valid_tipo_persona():
    assert is_valid_tipo_persona("PJ") is True
    assert is_valid_tipo_persona("PN") is True
    assert is_valid_tipo_persona(None) is True
    assert is_valid_tipo_persona("AMB") is False
