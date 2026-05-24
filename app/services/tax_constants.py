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
