"""
Tabla de Retención en la Fuente — Colombia 2026

Source: Carolina García, Contadora Pública (H&G Abogados y Contadores)
UVT 2026: $52.374

Usage:
    from app.services.retencion_table import get_retencion_rate, RetencionConcepto

    rate = get_retencion_rate(
        concepto=RetencionConcepto.SERVICIOS_GENERALES,
        es_declarante=True,
        base_pesos=1_500_000,
    )
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

UVT_2026: int = 52_374


class RetencionConcepto(str, Enum):
    COMPRAS_GENERALES = "compras_generales"
    COMPRAS_TARJETA = "compras_tarjeta"
    COMPRAS_AGRICOLAS_SIN_PROCESO = "compras_agricolas_sin_proceso"
    COMPRAS_AGRICOLAS_CON_PROCESO = "compras_agricolas_con_proceso"
    COMPRAS_COMBUSTIBLES = "compras_combustibles"
    COMPRAS_VEHICULOS = "compras_vehiculos"
    COMPRAS_BIENES_RAICES_VIVIENDA = "compras_bienes_raices_vivienda"
    COMPRAS_BIENES_RAICES_NO_VIVIENDA = "compras_bienes_raices_no_vivienda"
    SERVICIOS_GENERALES = "servicios_generales"
    SERVICIOS_TRANSPORTE_CARGA = "servicios_transporte_carga"
    SERVICIOS_TRANSPORTE_TERRESTRE = "servicios_transporte_terrestre"
    SERVICIOS_TRANSPORTE_AEREO_MARITIMO = "servicios_transporte_aereo_maritimo"
    SERVICIOS_TEMPORALES = "servicios_temporales"
    SERVICIOS_VIGILANCIA_ASEO = "servicios_vigilancia_aseo"
    SERVICIOS_IPS = "servicios_ips"
    SERVICIOS_HOTELES_RESTAURANTES = "servicios_hoteles_restaurantes"
    ARRENDAMIENTO_MUEBLES = "arrendamiento_muebles"
    ARRENDAMIENTO_INMUEBLES = "arrendamiento_inmuebles"
    OTROS_INGRESOS = "otros_ingresos"
    HONORARIOS_COMISIONES = "honorarios_comisiones"
    SOFTWARE_LICENCIAMIENTO = "software_licenciamiento"
    SOFTWARE_DESARROLLO = "software_desarrollo"
    INTERESES_RENDIMIENTOS = "intereses_rendimientos"
    RENDIMIENTOS_RENTA_FIJA = "rendimientos_renta_fija"
    LOTERIAS_RIFAS = "loterias_rifas"
    CONSTRUCCION_URBANIZACION = "construccion_urbanizacion"
    RETENCION_IVA_SERVICIOS = "retencion_iva_servicios"
    RETENCION_IVA_COMPRAS = "retencion_iva_compras"


@dataclass(frozen=True)
class RetencionRegla:
    concepto: RetencionConcepto
    tarifa: Decimal
    base_minima_uvt: int
    # None = same rate applies regardless of declarant status
    aplica_declarante: Optional[bool]


# Tabla completa 2026 — UVT $52.374
# Source: Carolina García, H&G Abogados y Contadores
_TABLA: list[RetencionRegla] = [
    # ── Compras ──────────────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.COMPRAS_GENERALES, Decimal("0.025"), 10, True),
    RetencionRegla(RetencionConcepto.COMPRAS_GENERALES, Decimal("0.035"), 10, False),
    RetencionRegla(RetencionConcepto.COMPRAS_TARJETA, Decimal("0.015"), 0, None),
    RetencionRegla(
        RetencionConcepto.COMPRAS_AGRICOLAS_SIN_PROCESO, Decimal("0.015"), 70, None
    ),
    RetencionRegla(
        RetencionConcepto.COMPRAS_AGRICOLAS_CON_PROCESO, Decimal("0.025"), 10, True
    ),
    RetencionRegla(
        RetencionConcepto.COMPRAS_AGRICOLAS_CON_PROCESO, Decimal("0.035"), 10, False
    ),
    RetencionRegla(RetencionConcepto.COMPRAS_COMBUSTIBLES, Decimal("0.001"), 0, None),
    RetencionRegla(RetencionConcepto.COMPRAS_VEHICULOS, Decimal("0.01"), 0, None),
    RetencionRegla(
        RetencionConcepto.COMPRAS_BIENES_RAICES_VIVIENDA, Decimal("0.01"), 0, None
    ),
    RetencionRegla(
        RetencionConcepto.COMPRAS_BIENES_RAICES_NO_VIVIENDA, Decimal("0.025"), 0, None
    ),
    # ── Servicios ────────────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.SERVICIOS_GENERALES, Decimal("0.04"), 2, True),
    RetencionRegla(RetencionConcepto.SERVICIOS_GENERALES, Decimal("0.06"), 2, False),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_TRANSPORTE_CARGA, Decimal("0.01"), 2, None
    ),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_TRANSPORTE_TERRESTRE, Decimal("0.035"), 10, None
    ),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_TRANSPORTE_AEREO_MARITIMO, Decimal("0.01"), 2, None
    ),
    RetencionRegla(RetencionConcepto.SERVICIOS_TEMPORALES, Decimal("0.01"), 2, None),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_VIGILANCIA_ASEO, Decimal("0.02"), 2, None
    ),
    RetencionRegla(RetencionConcepto.SERVICIOS_IPS, Decimal("0.02"), 2, None),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_HOTELES_RESTAURANTES, Decimal("0.035"), 2, None
    ),
    # ── Arrendamientos ───────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.ARRENDAMIENTO_MUEBLES, Decimal("0.04"), 0, None),
    RetencionRegla(
        RetencionConcepto.ARRENDAMIENTO_INMUEBLES, Decimal("0.035"), 10, None
    ),
    # ── Otros ingresos ───────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.OTROS_INGRESOS, Decimal("0.025"), 10, True),
    RetencionRegla(RetencionConcepto.OTROS_INGRESOS, Decimal("0.035"), 10, False),
    # ── Honorarios y comisiones ──────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.HONORARIOS_COMISIONES, Decimal("0.11"), 0, True),
    RetencionRegla(RetencionConcepto.HONORARIOS_COMISIONES, Decimal("0.10"), 0, False),
    # ── Software ─────────────────────────────────────────────────────────────
    RetencionRegla(
        RetencionConcepto.SOFTWARE_LICENCIAMIENTO, Decimal("0.035"), 0, None
    ),
    RetencionRegla(RetencionConcepto.SOFTWARE_DESARROLLO, Decimal("0.035"), 0, True),
    RetencionRegla(RetencionConcepto.SOFTWARE_DESARROLLO, Decimal("0.11"), 0, False),
    # ── Financieros ──────────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.INTERESES_RENDIMIENTOS, Decimal("0.07"), 0, None),
    RetencionRegla(RetencionConcepto.RENDIMIENTOS_RENTA_FIJA, Decimal("0.04"), 0, None),
    # ── Otros ────────────────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.LOTERIAS_RIFAS, Decimal("0.20"), 48, None),
    RetencionRegla(
        RetencionConcepto.CONSTRUCCION_URBANIZACION, Decimal("0.02"), 10, None
    ),
    # ── Retención de IVA ─────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.RETENCION_IVA_SERVICIOS, Decimal("0.15"), 2, None),
    RetencionRegla(RetencionConcepto.RETENCION_IVA_COMPRAS, Decimal("0.15"), 10, None),
]


def get_base_minima_pesos(base_minima_uvt: int) -> int:
    return base_minima_uvt * UVT_2026


def get_retencion_rate(
    concepto: RetencionConcepto,
    es_declarante: bool,
    base_pesos: float,
) -> Optional[Decimal]:
    """
    Return the applicable retention rate.

    Returns None if base_pesos is below the minimum threshold or no rule matches.

    Args:
        concepto: The retention concept.
        es_declarante: True if the recipient is a declarante de renta.
        base_pesos: Taxable base in Colombian pesos.

    Returns:
        Decimal rate (e.g. Decimal("0.04") for 4%), or None if not applicable.
    """
    for regla in _TABLA:
        if regla.concepto != concepto:
            continue
        if (
            regla.aplica_declarante is not None
            and regla.aplica_declarante != es_declarante
        ):
            continue
        if base_pesos < get_base_minima_pesos(regla.base_minima_uvt):
            return None
        return regla.tarifa
    return None


def calcular_retencion(
    concepto: RetencionConcepto,
    es_declarante: bool,
    base_pesos: Decimal,
) -> Decimal:
    """
    Calculate retention amount. Returns Decimal("0") if below threshold or no rule.
    """
    rate = get_retencion_rate(concepto, es_declarante, float(base_pesos))
    if rate is None:
        return Decimal("0")
    return (base_pesos * rate).quantize(Decimal("0.01"))
