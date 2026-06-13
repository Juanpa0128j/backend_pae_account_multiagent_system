"""
Tax-related constants shared across services and agents.

Centralizes vocabulary used to discriminate IVA treatment per ingreso line
(Art. 420, 477, 478, 481, 490 ET — Estatuto Tributario colombiano).

`tipo_iva` is a small enum-like vocabulary attached to every posted
transaction whose journal touches class 4 (Ingresos). It enables correct
prorrateo (Art. 490 ET) when a taxpayer mixes operations subject to
different IVA regimes.

Values
------
gravado_19    Operación gravada a la tarifa general (19%).
gravado_5     Operación gravada a la tarifa diferencial 5%
              (e.g. ciertos alimentos básicos — Art. 468-1 ET).
exento        Bienes/servicios exentos (Art. 477/478 ET). Tarifa 0%
              CON derecho a IVA descontable.
excluido      Bienes/servicios excluidos (Art. 424 ET). Sin IVA y SIN
              derecho a IVA descontable.
exportacion   Exportaciones (Art. 481 ET). Tratamiento exento con
              derecho a descontable.
no_gravado    Operación no gravada en general (ingresos no operacionales
              fuera del hecho generador del IVA).

Why a string column instead of a FK? Vocabulary is closed, very small,
and queried as a filter; an Enum here would force an alembic migration
every time DIAN amends a tarifa. A `CHECK` constraint enforces the set.
"""

from __future__ import annotations

from typing import Final

TIPO_IVA_GRAVADO_19: Final[str] = "gravado_19"
TIPO_IVA_GRAVADO_5: Final[str] = "gravado_5"
TIPO_IVA_EXENTO: Final[str] = "exento"
TIPO_IVA_EXCLUIDO: Final[str] = "excluido"
TIPO_IVA_EXPORTACION: Final[str] = "exportacion"
TIPO_IVA_NO_GRAVADO: Final[str] = "no_gravado"

TIPOS_IVA_VALIDOS: Final[frozenset[str]] = frozenset(
    {
        TIPO_IVA_GRAVADO_19,
        TIPO_IVA_GRAVADO_5,
        TIPO_IVA_EXENTO,
        TIPO_IVA_EXCLUIDO,
        TIPO_IVA_EXPORTACION,
        TIPO_IVA_NO_GRAVADO,
    }
)

# Cuentas PUC clave para inferir tipo_iva desde el asiento contable.
CUENTA_IVA_GENERADO_19: Final[str] = "240805"
CUENTA_IVA_GENERADO_5: Final[str] = "240807"
CUENTA_IVA_DESCONTABLE: Final[str] = "240802"

# Subcuentas de ingreso típicas para exportaciones / cuenta resumen.
CUENTA_INGRESOS_EXPORTACION: Final[str] = "4175"


def is_valid_tipo_iva(value: str | None) -> bool:
    """True when value is a recognized tipo_iva or explicitly None."""
    return value is None or value in TIPOS_IVA_VALIDOS


def infer_tipo_iva_from_journal(
    journal_entries: list[dict] | None,
    descripcion: str | None = None,
) -> str | None:
    """Infer ``tipo_iva`` from a contador-built journal asiento.

    Heuristics (in order):
      1. Credit to ``240805`` -> gravado_19.
      2. Credit to ``240807`` -> gravado_5.
      3. Descripcion mentions exportación / credit to ``4175`` -> exportacion.
      4. Credit to class 4 without any IVA generado credit -> None
         (no_clasificado, builder must default conservatively).
      5. No class-4 credit -> None (not an ingreso).

    Returns ``None`` when no signal is reliable enough to classify; this
    leaves the column NULL so the F300 builder surfaces it for review.
    """
    if not journal_entries:
        return None

    def _amount(entry: dict, key: str) -> float:
        try:
            return float(entry.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    has_iva_19 = any(
        str(e.get("cuenta") or "").startswith(CUENTA_IVA_GENERADO_19)
        and _amount(e, "credito") > 0
        for e in journal_entries
    )
    has_iva_5 = any(
        str(e.get("cuenta") or "").startswith(CUENTA_IVA_GENERADO_5)
        and _amount(e, "credito") > 0
        for e in journal_entries
    )
    has_export_account = any(
        str(e.get("cuenta") or "").startswith(CUENTA_INGRESOS_EXPORTACION)
        and _amount(e, "credito") > 0
        for e in journal_entries
    )
    has_class4_credit = any(
        str(e.get("cuenta") or "").startswith("4") and _amount(e, "credito") > 0
        for e in journal_entries
    )

    desc_lower = (descripcion or "").lower()
    if has_iva_19:
        return TIPO_IVA_GRAVADO_19
    if has_iva_5:
        return TIPO_IVA_GRAVADO_5
    if has_export_account or "exportaci" in desc_lower:
        return TIPO_IVA_EXPORTACION
    if not has_class4_credit:
        # Asiento de gasto/pago: tipo_iva no aplica al ingreso.
        return None
    # Class-4 credit con cero IVA generado: ambiguo (exento vs excluido vs
    # no_gravado). Dejar NULL para revisión humana.
    return None


# ---------------------------------------------------------------------------
# F350 retención — concepto + tipo_persona inference
# ---------------------------------------------------------------------------

TIPO_PERSONA_PJ: Final[str] = "PJ"
TIPO_PERSONA_PN: Final[str] = "PN"
APLICA_AMBOS: Final[str] = "AMB"

TIPOS_PERSONA_VALIDOS: Final[frozenset[str]] = frozenset(
    {TIPO_PERSONA_PJ, TIPO_PERSONA_PN}
)
APLICA_A_VALIDOS: Final[frozenset[str]] = frozenset(
    {TIPO_PERSONA_PJ, TIPO_PERSONA_PN, APLICA_AMBOS}
)


# PUC-prefix → categoria del concepto retención. Used as primary signal
# when the contador already classified the expense correctly.
_PUC_PREFIX_TO_CATEGORIA: Final[dict[str, str]] = {
    "5135": "arrendamiento",  # gastos de arrendamiento
    "5140": "arrendamiento",
    "511525": "servicios",  # servicios técnicos
    "511505": "honorarios",  # honorarios / asesoría
    "511510": "honorarios",  # comisiones
    "5110": "honorarios",
    "5111": "honorarios",
    "5115": "servicios",
    "143": "compras",  # inventarios — compras de bienes
    "6135": "compras",  # costo de mercancías
    "6205": "compras",  # costo de ventas
    "5145": "servicios",  # servicios públicos
    "5160": "servicios",
}


def is_valid_tipo_persona(value: str | None) -> bool:
    """True when value is PJ, PN, or None."""
    return value is None or value in TIPOS_PERSONA_VALIDOS


def is_valid_aplica_a(value: str | None) -> bool:
    """True when value is PJ, PN, AMB, or None."""
    return value is None or value in APLICA_A_VALIDOS


def infer_tipo_persona_from_nit(nit: str | None) -> str | None:
    """Heuristic tipo_persona from a Colombian NIT.

    Empresa NITs (personas jurídicas) typically start with 8 or 9 and have
    9–10 base digits before the verification digit. Cédulas (personas
    naturales) are 6–10 digits without the empresarial prefix.

    Returns ``None`` when the NIT is empty / non-numeric so the caller can
    fall back to a safe default + warning.
    """
    if not nit:
        return None
    cleaned = "".join(ch for ch in nit if ch.isdigit())
    if not cleaned:
        return None
    # Strip a trailing dígito de verificación if length suggests it.
    base = cleaned[:-1] if len(cleaned) in (10, 11) else cleaned
    if not base:
        return None
    if base.startswith(("8", "9")) and len(base) >= 8:
        return TIPO_PERSONA_PJ
    return TIPO_PERSONA_PN


def categoria_retencion_from_puc(cuenta_puc: str | None) -> str | None:
    """Map a PUC code to a retención categoria via longest-prefix match.

    Returns ``'honorarios' | 'servicios' | 'arrendamiento' | 'compras'`` or
    ``None`` when no prefix matches. Shared by the tributario agent (to pick the
    correct retefuente tarifa — honorarios 11% vs servicios 4% vs compras 2.5%)
    and by :func:`infer_concepto_retencion`.
    """
    if not cuenta_puc:
        return None
    cuenta_puc = str(cuenta_puc)
    # Longest-prefix wins (e.g. 511505 honorarios before 5115 servicios).
    for prefix in sorted(_PUC_PREFIX_TO_CATEGORIA.keys(), key=len, reverse=True):
        if cuenta_puc.startswith(prefix):
            return _PUC_PREFIX_TO_CATEGORIA[prefix]
    return None


def infer_concepto_retencion(
    cuenta_puc: str | None,
    tipo_persona: str | None,
    *,
    descripcion: str | None = None,
) -> str | None:
    """Infer ``concepto_retencion`` (tax_concepts.code) from PUC + tipo_persona.

    Resolution order:
      1. PUC prefix → categoria.
      2. categoria + tipo_persona → concept code.

    Returns ``None`` when no rule matches — F350 builder will emit a warning.
    """
    if not cuenta_puc:
        return None
    cuenta_puc = str(cuenta_puc)
    categoria: str | None = categoria_retencion_from_puc(cuenta_puc)

    desc_lower = (descripcion or "").lower()
    if categoria is None:
        if (
            "hidrocarbur" in desc_lower
            or "petróle" in desc_lower
            or "petrole" in desc_lower
        ):
            return "hidrocarburos"
        if "carbón" in desc_lower or "carbon" in desc_lower:
            return "carbon"
        if "minera" in desc_lower:
            return "minerales"
        if "publicidad" in desc_lower and (
            "online" in desc_lower or "digital" in desc_lower
        ):
            return "pes_pub_online"
        if "servicio digital" in desc_lower or "plataforma digital" in desc_lower:
            return "pes_svcs_dig"
        return None

    # Map (categoria, tipo_persona) → concept code.
    persona = tipo_persona or TIPO_PERSONA_PJ
    mapping: dict[tuple[str, str], str] = {
        ("compras", TIPO_PERSONA_PJ): "compras_pj",
        ("compras", TIPO_PERSONA_PN): "compras_pn",
        ("servicios", TIPO_PERSONA_PJ): "servicios_pj",
        ("servicios", TIPO_PERSONA_PN): "serv_pn_decl",
        ("honorarios", TIPO_PERSONA_PJ): "honorarios_pj",
        ("honorarios", TIPO_PERSONA_PN): "honorarios_pn",
        ("arrendamiento", TIPO_PERSONA_PJ): "arrendamiento_pj",
        ("arrendamiento", TIPO_PERSONA_PN): "arrendamiento_pn",
    }
    return mapping.get((categoria, persona))
