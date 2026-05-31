"""
Tabla de Retención en la Fuente — Colombia 2026

Source: Carolina García, Contadora Pública (H&G Abogados y Contadores)
UVT 2026: $52.374

Vigencia dual (Decreto 0572 suspendido 07-05-2026):
  - Pagos con fecha 01-05-2026 a 07-05-2026: tabla original
  - Pagos con fecha >= 08-05-2026: tabla modificada (solo cambian bases mínimas)

Usage:
    from app.services.retencion_table import get_retencion_rate, RetencionConcepto
    import datetime

    rate = get_retencion_rate(
        concepto=RetencionConcepto.SERVICIOS_GENERALES,
        es_declarante=True,
        base_pesos=1_500_000,
        fecha_pago=datetime.date(2026, 5, 10),
    )
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

_FECHA_NUEVA_TABLA = datetime.date(2026, 5, 8)

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


# Tabla original 2026 — UVT $52.374 — vigente hasta 07-05-2026
# Source: Carolina García, H&G Abogados y Contadores
_TABLA_ORIGINAL: list[RetencionRegla] = [
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

# Tabla modificada — vigente desde 08-05-2026
# Suspensión decreto 0572 (07-05-2026). Solo cambian bases mínimas, no tarifas.
# Source: Carolina García, H&G Abogados y Contadores
_TABLA_MAYO_2026: list[RetencionRegla] = [
    # ── Compras ──────────────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.COMPRAS_GENERALES, Decimal("0.025"), 27, True),
    RetencionRegla(RetencionConcepto.COMPRAS_GENERALES, Decimal("0.035"), 27, False),
    RetencionRegla(RetencionConcepto.COMPRAS_TARJETA, Decimal("0.015"), 0, None),
    RetencionRegla(
        RetencionConcepto.COMPRAS_AGRICOLAS_SIN_PROCESO, Decimal("0.015"), 92, None
    ),
    RetencionRegla(
        RetencionConcepto.COMPRAS_AGRICOLAS_CON_PROCESO, Decimal("0.025"), 27, True
    ),
    RetencionRegla(
        RetencionConcepto.COMPRAS_AGRICOLAS_CON_PROCESO, Decimal("0.035"), 27, False
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
    RetencionRegla(RetencionConcepto.SERVICIOS_GENERALES, Decimal("0.04"), 4, True),
    RetencionRegla(RetencionConcepto.SERVICIOS_GENERALES, Decimal("0.06"), 4, False),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_TRANSPORTE_CARGA, Decimal("0.01"), 4, None
    ),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_TRANSPORTE_TERRESTRE, Decimal("0.035"), 27, None
    ),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_TRANSPORTE_AEREO_MARITIMO, Decimal("0.01"), 4, None
    ),
    RetencionRegla(RetencionConcepto.SERVICIOS_TEMPORALES, Decimal("0.01"), 4, None),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_VIGILANCIA_ASEO, Decimal("0.02"), 4, None
    ),
    RetencionRegla(RetencionConcepto.SERVICIOS_IPS, Decimal("0.02"), 4, None),
    RetencionRegla(
        RetencionConcepto.SERVICIOS_HOTELES_RESTAURANTES, Decimal("0.035"), 4, None
    ),
    # ── Arrendamientos ───────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.ARRENDAMIENTO_MUEBLES, Decimal("0.04"), 0, None),
    RetencionRegla(
        RetencionConcepto.ARRENDAMIENTO_INMUEBLES, Decimal("0.035"), 27, None
    ),
    # ── Otros ingresos ───────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.OTROS_INGRESOS, Decimal("0.025"), 27, True),
    RetencionRegla(RetencionConcepto.OTROS_INGRESOS, Decimal("0.035"), 27, False),
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
        RetencionConcepto.CONSTRUCCION_URBANIZACION, Decimal("0.02"), 27, None
    ),
    # ── Retención de IVA ─────────────────────────────────────────────────────
    RetencionRegla(RetencionConcepto.RETENCION_IVA_SERVICIOS, Decimal("0.15"), 4, None),
    RetencionRegla(RetencionConcepto.RETENCION_IVA_COMPRAS, Decimal("0.15"), 27, None),
]


def _get_tabla(fecha_pago: Optional[datetime.date]) -> list[RetencionRegla]:
    if fecha_pago is not None and fecha_pago >= _FECHA_NUEVA_TABLA:
        return _TABLA_MAYO_2026
    return _TABLA_ORIGINAL


# Backward-compat alias: points to original table (pre-May-8 behavior)
_TABLA = _TABLA_ORIGINAL


def get_base_minima_pesos(
    base_minima_uvt: int, uvt_value: float | None = None, fiscal_year: int | None = None
) -> int:
    """
    Calculate the minimum taxable base in pesos from UVT units.

    Args:
        base_minima_uvt: Minimum base in UVT units.
        uvt_value: Optional UVT value (in pesos). If provided, use this directly.
        fiscal_year: Fiscal year for DB lookup if uvt_value is None. Defaults to current year.

    Returns:
        Minimum base in Colombian pesos.
    """
    if uvt_value is not None:
        return int(base_minima_uvt * uvt_value)

    # Query DB for UVT if not provided
    if fiscal_year is None:
        fiscal_year = datetime.date.today().year

    try:
        from app.core.database import SessionLocal
        from app.services import db_service as _db_svc

        _db = SessionLocal()
        try:
            _uvt_db = _db_svc.get_uvt(_db, fiscal_year)
            if _uvt_db is not None:
                logger.debug(
                    "get_base_minima_pesos: UVT %d from DB = %s", fiscal_year, _uvt_db
                )
                return int(base_minima_uvt * _uvt_db)
            else:
                logger.debug(
                    "get_base_minima_pesos: UVT %d not in DB, using fallback %s",
                    fiscal_year,
                    UVT_2026,
                )
        finally:
            _db.close()
    except Exception as e:
        logger.warning(
            "get_base_minima_pesos: DB query failed for year %d, using fallback: %s",
            fiscal_year,
            e,
        )

    return base_minima_uvt * UVT_2026


def get_retencion_rate(
    concepto: RetencionConcepto,
    es_declarante: bool,
    base_pesos: float,
    fecha_pago: Optional[datetime.date] = None,
    uvt_value: float | None = None,
    fiscal_year: int | None = None,
) -> Optional[Decimal]:
    """
    Return the applicable retention rate.

    Returns None if base_pesos is below the minimum threshold or no rule matches.

    Args:
        concepto: The retention concept.
        es_declarante: True if the recipient is a declarante de renta.
        base_pesos: Taxable base in Colombian pesos.
        fecha_pago: Payment date. Determines which table applies:
            - None or < 2026-05-08: tabla original (Decreto 0572 vigente)
            - >= 2026-05-08: tabla modificada (suspensión Decreto 0572)
        uvt_value: Optional UVT value in pesos. If provided, use this for calculations.
        fiscal_year: Fiscal year for DB lookup if uvt_value is None. Defaults to current year.

    Returns:
        Decimal rate (e.g. Decimal("0.04") for 4%), or None if not applicable.
    """
    tabla = _get_tabla(fecha_pago)
    for regla in tabla:
        if regla.concepto != concepto:
            continue
        if (
            regla.aplica_declarante is not None
            and regla.aplica_declarante != es_declarante
        ):
            continue
        if base_pesos < get_base_minima_pesos(
            regla.base_minima_uvt, uvt_value=uvt_value, fiscal_year=fiscal_year
        ):
            return None
        return regla.tarifa
    return None


def calcular_retencion(
    concepto: RetencionConcepto,
    es_declarante: bool,
    base_pesos: Decimal,
    fecha_pago: Optional[datetime.date] = None,
) -> Decimal:
    """
    Calculate retention amount. Returns Decimal("0") if below threshold or no rule.

    Args:
        fecha_pago: Payment date. Determines which table applies (see get_retencion_rate).
    """
    rate = get_retencion_rate(concepto, es_declarante, float(base_pesos), fecha_pago)
    if rate is None:
        return Decimal("0")
    return (base_pesos * rate).quantize(Decimal("0.01"))
