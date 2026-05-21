"""Tests for retencion_table dual-table logic (Decreto 0572 suspension)."""

import datetime
from decimal import Decimal

import pytest

from app.services.retencion_table import (
    RetencionConcepto,
    calcular_retencion,
    get_retencion_rate,
)

FECHA_PRE = datetime.date(2026, 5, 7)  # tabla original
FECHA_POST = datetime.date(2026, 5, 8)  # tabla nueva


class TestComprasGeneralesBaseSwitch:
    """Compras generales: base 10 UVT original → 27 UVT nueva."""

    def test_declarante_pre_mayo8_base_10uvt_aplica(self):
        # 10 UVT = 523.740. Base original era 10 UVT → aplica
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=True,
            base_pesos=600_000,
            fecha_pago=FECHA_PRE,
        )
        assert rate == Decimal("0.025")

    def test_declarante_post_mayo8_base_bajo_27uvt_no_aplica(self):
        # 600.000 < 27 UVT = 1.414.098 → no aplica
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=True,
            base_pesos=600_000,
            fecha_pago=FECHA_POST,
        )
        assert rate is None

    def test_declarante_post_mayo8_base_sobre_27uvt_aplica(self):
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=True,
            base_pesos=1_500_000,
            fecha_pago=FECHA_POST,
        )
        assert rate == Decimal("0.025")

    def test_no_declarante_post_mayo8(self):
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=False,
            base_pesos=1_500_000,
            fecha_pago=FECHA_POST,
        )
        assert rate == Decimal("0.035")


class TestServiciosGeneralesBaseSwitch:
    """Servicios generales: base 2 UVT original → 4 UVT nueva."""

    def test_pre_mayo8_base_2uvt_aplica(self):
        # 2 UVT = 104.748. 200.000 > umbral → aplica
        rate = get_retencion_rate(
            RetencionConcepto.SERVICIOS_GENERALES,
            es_declarante=True,
            base_pesos=200_000,
            fecha_pago=FECHA_PRE,
        )
        assert rate == Decimal("0.04")

    def test_post_mayo8_base_bajo_4uvt_no_aplica(self):
        # 4 UVT = 209.496. 200.000 < umbral → no aplica
        rate = get_retencion_rate(
            RetencionConcepto.SERVICIOS_GENERALES,
            es_declarante=True,
            base_pesos=200_000,
            fecha_pago=FECHA_POST,
        )
        assert rate is None

    def test_post_mayo8_base_sobre_4uvt_aplica(self):
        rate = get_retencion_rate(
            RetencionConcepto.SERVICIOS_GENERALES,
            es_declarante=True,
            base_pesos=250_000,
            fecha_pago=FECHA_POST,
        )
        assert rate == Decimal("0.04")


class TestAgricolasBaseSwitch:
    """Agrícolas sin proceso: base 70 UVT original → 92 UVT nueva."""

    def test_pre_mayo8_base_70uvt_aplica(self):
        # 70 UVT = 3.666.180. 4.000.000 > umbral → aplica
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_AGRICOLAS_SIN_PROCESO,
            es_declarante=True,
            base_pesos=4_000_000,
            fecha_pago=FECHA_PRE,
        )
        assert rate == Decimal("0.015")

    def test_post_mayo8_base_bajo_92uvt_no_aplica(self):
        # 92 UVT = 4.818.408. 4.000.000 < umbral → no aplica
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_AGRICOLAS_SIN_PROCESO,
            es_declarante=True,
            base_pesos=4_000_000,
            fecha_pago=FECHA_POST,
        )
        assert rate is None

    def test_post_mayo8_base_sobre_92uvt_aplica(self):
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_AGRICOLAS_SIN_PROCESO,
            es_declarante=True,
            base_pesos=5_000_000,
            fecha_pago=FECHA_POST,
        )
        assert rate == Decimal("0.015")


class TestConceptosSinCambio:
    """Conceptos cuyas bases no cambiaron deben ser iguales en ambas tablas."""

    @pytest.mark.parametrize(
        "concepto,es_declarante,base",
        [
            (RetencionConcepto.COMPRAS_TARJETA, True, 100_000),
            (RetencionConcepto.COMPRAS_COMBUSTIBLES, True, 50_000),
            (RetencionConcepto.HONORARIOS_COMISIONES, True, 500_000),
            (RetencionConcepto.INTERESES_RENDIMIENTOS, True, 100_000),
            (RetencionConcepto.LOTERIAS_RIFAS, True, 3_000_000),
        ],
    )
    def test_tarifa_igual_pre_y_post(self, concepto, es_declarante, base):
        rate_pre = get_retencion_rate(concepto, es_declarante, base, FECHA_PRE)
        rate_post = get_retencion_rate(concepto, es_declarante, base, FECHA_POST)
        assert rate_pre == rate_post

    def test_tarifa_no_cambia(self):
        """Verificar que las tarifas (%) no cambiaron, solo las bases."""
        rate_pre = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES, True, 2_000_000, FECHA_PRE
        )
        rate_post = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES, True, 2_000_000, FECHA_POST
        )
        assert rate_pre == rate_post == Decimal("0.025")


class TestSinFechaPago:
    """Sin fecha_pago → comportamiento original (backward compat)."""

    def test_sin_fecha_usa_tabla_original(self):
        # Base 600.000 aplica con tabla original (10 UVT), no con nueva (27 UVT)
        rate = get_retencion_rate(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=True,
            base_pesos=600_000,
        )
        assert rate == Decimal("0.025")


class TestCalcualarRetencion:
    """calcular_retencion respeta fecha_pago."""

    def test_calculo_post_mayo8(self):
        monto = calcular_retencion(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=True,
            base_pesos=Decimal("2000000"),
            fecha_pago=FECHA_POST,
        )
        assert monto == Decimal("50000.00")  # 2.000.000 * 2.5%

    def test_calculo_cero_bajo_umbral_post(self):
        monto = calcular_retencion(
            RetencionConcepto.COMPRAS_GENERALES,
            es_declarante=True,
            base_pesos=Decimal("600000"),
            fecha_pago=FECHA_POST,
        )
        assert monto == Decimal("0")
