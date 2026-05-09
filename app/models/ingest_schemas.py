"""
Polymorphic content schemas for document ingestion.

Each document type produces data with a different structure. These Pydantic
models define the expected output from Gemini for each document type.

All top-level content schemas include `informacion_adicional` — an open-ended
field where the LLM captures anything else relevant for downstream processing
(contador PUC classification, tributario tax calculations, auditor review).
"""

from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _parse_decimal(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        v = v.strip().replace("\xa0", "").replace(" ", "")
        if not v:
            return None
        # Colombian format: 3.075.206,00 → 3075206.00
        if "," in v and v.count(".") >= 1:
            v = v.replace(".", "").replace(",", ".")
        elif "," in v:
            v = v.replace(",", ".")
        try:
            return Decimal(v)
        except Exception:
            return None
    if isinstance(v, dict):
        # LLM sometimes returns a breakdown dict instead of a flat total.
        # Sum all numeric leaf values as a best-effort total.
        total = sum(float(val) for val in v.values() if isinstance(val, (int, float)))
        return Decimal(str(total)) if total else None
    return v


# ---------------------------------------------------------------------------
# Base class — coerces informacion_adicional strings to dict
# ---------------------------------------------------------------------------


class ContentBase(BaseModel):
    """Base for all content schemas. Coerces informacion_adicional to dict if LLM returns a string."""

    @model_validator(mode="before")
    @classmethod
    def coerce_informacion_adicional(cls, values):
        if isinstance(values, dict):
            v = values.get("informacion_adicional")
            if isinstance(v, str):
                values["informacion_adicional"] = {"value": v}
        return values


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class NitEntidad(BaseModel):
    razon_social: str = Field(description="Company name")
    nit: Optional[str] = Field(
        None, description="NIT with verification digit (e.g. 900123456-7)"
    )


class ItemImpuesto(BaseModel):
    tipo: Optional[str] = Field(None, description="Tax type: IVA, INC, ICA, IBUA, otro")
    base_gravable: Optional[Decimal] = Field(None, description="Taxable base")
    tarifa: Optional[Decimal] = Field(None, description="Rate as decimal (0.19, 0.05)")
    valor: Optional[Decimal] = Field(None, description="Tax amount")

    @field_validator("base_gravable", "tarifa", "valor", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class FacturaItem(BaseModel):
    descripcion: str = Field(description="Item description")
    cantidad: Optional[Decimal] = Field(None, description="Quantity")
    unidad_medida: Optional[str] = Field(None, description="Unit of measure")
    valor_unitario: Optional[Decimal] = Field(None, description="Unit price")
    descuento: Optional[Decimal] = Field(None, description="Discount amount")
    valor_total_sin_impuesto: Optional[Decimal] = Field(
        None, description="Subtotal without tax"
    )
    impuestos: Optional[List[ItemImpuesto]] = Field(
        None, description="Taxes on this item"
    )
    codigo_producto: Optional[str] = Field(None, description="Product code")
    es_gravado: Optional[bool] = Field(None, description="Subject to IVA")
    es_excluido: Optional[bool] = Field(None, description="Excluded from IVA")
    es_exento: Optional[bool] = Field(None, description="Exempt from IVA")

    @field_validator(
        "cantidad",
        "valor_unitario",
        "descuento",
        "valor_total_sin_impuesto",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class FacturaTotales(BaseModel):
    subtotal_sin_impuestos: Optional[Decimal] = Field(None)
    total_descuentos: Optional[Decimal] = Field(None)
    total_iva: Optional[Decimal] = Field(None)
    total_inc: Optional[Decimal] = Field(None)
    total_otros_impuestos: Optional[Decimal] = Field(None)
    total_retenciones: Optional[Decimal] = Field(None)
    total_factura: Optional[Decimal] = Field(None)
    total_a_pagar: Optional[Decimal] = Field(None)

    @field_validator(
        "subtotal_sin_impuestos",
        "total_descuentos",
        "total_iva",
        "total_inc",
        "total_otros_impuestos",
        "total_retenciones",
        "total_factura",
        "total_a_pagar",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class Retencion(BaseModel):
    tipo: Optional[str] = Field(None, description="retefuente | reteiva | reteica")
    base: Optional[Decimal] = Field(None)
    tarifa: Optional[Decimal] = Field(None)
    valor: Optional[Decimal] = Field(None)

    @field_validator("base", "tarifa", "valor", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 1. FacturaVentaContent — factura_venta
# ---------------------------------------------------------------------------


class EmisorFactura(BaseModel):
    razon_social: Optional[str] = Field(None)
    nit: Optional[str] = Field(None, description="NIT with DV (e.g. 900123456-7)")
    direccion: Optional[str] = Field(None)
    municipio: Optional[str] = Field(None)
    departamento: Optional[str] = Field(None)
    telefono: Optional[str] = Field(None)
    correo: Optional[str] = Field(None)
    regimen: Optional[str] = Field(
        None,
        description="responsable_iva | no_responsable_iva | regimen_simple | gran_contribuyente",
    )
    resolucion_facturacion: Optional[str] = Field(
        None, description="DIAN billing authorization number and date"
    )


class ReceptorFactura(BaseModel):
    razon_social: Optional[str] = Field(None)
    nit: Optional[str] = Field(None)
    direccion: Optional[str] = Field(None)
    municipio: Optional[str] = Field(None)
    departamento: Optional[str] = Field(None)
    correo: Optional[str] = Field(None)


class FacturaVentaContent(ContentBase):
    """Factura de venta emitida — DIAN electronic invoice."""

    consecutivo: Optional[str] = Field(
        None, description="Invoice number with DIAN prefix and authorized range"
    )
    cufe: Optional[str] = Field(
        None, description="Código Único de Facturación Electrónica"
    )
    qr_code: Optional[str] = Field(
        None, description="QR code URL or content from DIAN electronic invoice"
    )
    fecha_emision: Optional[str] = Field(None, description="YYYY-MM-DD")
    fecha_vencimiento: Optional[str] = Field(None, description="YYYY-MM-DD")
    forma_pago: Optional[str] = Field(None, description="contado | credito")
    medio_pago: Optional[str] = Field(
        None, description="efectivo | tarjeta_credito | transferencia | otro"
    )
    plazo_dias: Optional[int] = Field(None)
    emisor: Optional[EmisorFactura] = Field(None)
    receptor: Optional[ReceptorFactura] = Field(None)
    items: Optional[List[FacturaItem]] = Field(None)
    totales: Optional[FacturaTotales] = Field(None)
    retenciones_aplicadas: Optional[List[Retencion]] = Field(None)
    notas: Optional[str] = Field(None, description="Free text observations")
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None,
        description="Any other data relevant for downstream accounting: PUC classification clues, tax regime, CIIU codes, anomalies, references to DIAN resolutions, etc.",
    )


# ---------------------------------------------------------------------------
# 2. FacturaCompraContent — factura_compra
# ---------------------------------------------------------------------------


class FacturaCompraContent(ContentBase):
    """Factura de compra recibida — support document for costs/deductions."""

    consecutivo: Optional[str] = Field(None)
    cufe: Optional[str] = Field(None)
    qr_code: Optional[str] = Field(
        None, description="QR code URL or content from DIAN electronic invoice"
    )
    fecha_emision: Optional[str] = Field(None, description="YYYY-MM-DD")
    fecha_vencimiento: Optional[str] = Field(None, description="YYYY-MM-DD")
    forma_pago: Optional[str] = Field(None, description="contado | credito")
    medio_pago: Optional[str] = Field(None)
    plazo_dias: Optional[int] = Field(None)
    condiciones_pago: Optional[str] = Field(
        None,
        description="Condiciones de pago acordadas, p. ej. '30 días neto' o 'contado inmediato'",
    )
    proveedor: Optional[EmisorFactura] = Field(None, description="Supplier")
    empresa_receptora: Optional[ReceptorFactura] = Field(
        None, description="Receiving company"
    )
    items: Optional[List[FacturaItem]] = Field(None)
    totales: Optional[FacturaTotales] = Field(None)
    retenciones_aplicadas: Optional[List[Retencion]] = Field(None)
    documento_soporte: Optional[bool] = Field(
        None, description="True if support doc for non-invoicing party purchases"
    )
    notas: Optional[str] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None,
        description="Any other data relevant for downstream accounting: PUC classification clues, cost nature, deductibility, anomalies.",
    )


# ---------------------------------------------------------------------------
# 3. NotaCreditoContent — nota_credito
# ---------------------------------------------------------------------------


class FacturaReferencia(BaseModel):
    consecutivo: Optional[str] = Field(None)
    cufe: Optional[str] = Field(None)
    fecha: Optional[str] = Field(None, description="YYYY-MM-DD")


class NotaCreditoContent(ContentBase):
    """Nota crédito — partial or total reversal of an invoice."""

    consecutivo: Optional[str] = Field(None)
    cude: Optional[str] = Field(
        None, description="Código Único de Documento Electrónico"
    )
    fecha_emision: Optional[str] = Field(None, description="YYYY-MM-DD")
    factura_referencia: Optional[FacturaReferencia] = Field(None)
    concepto: Optional[str] = Field(
        None, description="devolucion | descuento | anulacion | correccion_valor | otro"
    )
    concepto_descripcion: Optional[str] = Field(None)
    emisor: Optional[EmisorFactura] = Field(None)
    receptor: Optional[ReceptorFactura] = Field(None)
    items: Optional[List[FacturaItem]] = Field(None)
    subtotal_ajustado: Optional[Decimal] = Field(None)
    total_iva_ajustado: Optional[Decimal] = Field(None)
    total_nota_credito: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "subtotal_ajustado", "total_iva_ajustado", "total_nota_credito", mode="before"
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 4. NotaDebitoContent — nota_debito
# ---------------------------------------------------------------------------


class NotaDebitoContent(ContentBase):
    """Nota débito — increases the value of a previously issued invoice."""

    consecutivo: Optional[str] = Field(None)
    cude: Optional[str] = Field(None)
    fecha_emision: Optional[str] = Field(None, description="YYYY-MM-DD")
    factura_referencia: Optional[FacturaReferencia] = Field(None)
    concepto: Optional[str] = Field(
        None, description="intereses | ajuste_precio | penalizacion | otro"
    )
    concepto_descripcion: Optional[str] = Field(None)
    emisor: Optional[EmisorFactura] = Field(None)
    receptor: Optional[ReceptorFactura] = Field(None)
    items: Optional[List[FacturaItem]] = Field(None)
    subtotal_adicionado: Optional[Decimal] = Field(None)
    total_iva_adicionado: Optional[Decimal] = Field(None)
    total_nota_debito: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "subtotal_adicionado",
        "total_iva_adicionado",
        "total_nota_debito",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 5. BankStatementContent — extracto_bancario
# ---------------------------------------------------------------------------


class BankMovement(BaseModel):
    fecha: str = Field(description="Date YYYY-MM-DD")
    descripcion: str = Field(description="Movement description")
    referencia: Optional[str] = Field(None, description="Reference number")
    tipo: Optional[str] = Field(None, description="debito | credito")
    debito: Optional[Decimal] = Field(None, ge=0)
    credito: Optional[Decimal] = Field(None, ge=0)
    saldo: Optional[Decimal] = Field(None, description="Running balance after movement")

    @field_validator("debito", "credito", "saldo", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class BankResumen(BaseModel):
    total_debitos: Optional[Decimal] = Field(None)
    total_creditos: Optional[Decimal] = Field(None)
    cantidad_debitos: Optional[int] = Field(None)
    cantidad_creditos: Optional[int] = Field(None)

    @field_validator("total_debitos", "total_creditos", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class BankStatementContent(ContentBase):
    """Content schema for bank statements."""

    entidad_financiera: Optional[str] = Field(None, description="Bank name")
    numero_cuenta: Optional[str] = Field(None, description="Account number")
    tipo_cuenta: Optional[str] = Field(None, description="corriente | ahorros | otro")
    titular: Optional[NitEntidad] = Field(None, description="Account holder")
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    saldo_inicial: Optional[Decimal] = Field(None)
    saldo_final: Optional[Decimal] = Field(None)
    movements: List[BankMovement] = Field(default_factory=list)
    resumen: Optional[BankResumen] = Field(None)
    gmf_cobrado: Optional[Decimal] = Field(
        None, description="4x1000 financial transactions tax charged"
    )
    intereses_generados: Optional[Decimal] = Field(None)
    retencion_fuente_rendimientos: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "saldo_inicial",
        "saldo_final",
        "gmf_cobrado",
        "intereses_generados",
        "retencion_fuente_rendimientos",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)

    # Legacy field aliases for backward compatibility
    @property
    def cuenta_bancaria(self) -> Optional[str]:
        return self.numero_cuenta

    @property
    def entidad_bancaria(self) -> Optional[str]:
        return self.entidad_financiera


# ---------------------------------------------------------------------------
# 6. TaxDeclarationContent — declaracion_iva
# ---------------------------------------------------------------------------


class TaxDeclarationContent(ContentBase):
    """Content schema for DIAN tax declarations (Formulario 300 IVA, 350 Retefuente, etc.)."""

    formulario: Optional[str] = Field(
        None, description="DIAN form number (e.g. '300' for IVA, '350' for retefuente)"
    )
    periodo: Optional[str] = Field(
        None, description="Tax period (e.g. '2026-01' bimestral)"
    )
    periodicidad: Optional[str] = Field(
        None, description="Frequency: anual | bimestral | cuatrimestral | mensual"
    )
    nit_declarante: Optional[str] = Field(None)
    base_gravable: Optional[Decimal] = Field(
        None, description="Total taxable base declared"
    )
    renglones: Optional[Dict[str, Decimal]] = Field(
        None, description="DIAN form row values keyed by row number"
    )
    impuestos_descontables: Optional[Dict[str, Any]] = Field(
        None,
        description="Discountable taxes detail by concept (compras_nacionales, importaciones, servicios, honorarios, etc.)",
    )
    total_a_pagar: Optional[Decimal] = Field(None)
    saldo_a_favor: Optional[Decimal] = Field(None)
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator("total_a_pagar", "saldo_a_favor", "base_gravable", mode="before")
    @classmethod
    def parse_total(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 7. DeclaracionICAContent — declaracion_ica
# ---------------------------------------------------------------------------


class ActividadEconomicaICA(BaseModel):
    codigo_ciiu: Optional[str] = Field(None)
    descripcion: Optional[str] = Field(None)
    tarifa_ica_por_mil: Optional[Decimal] = Field(
        None, description="Rate in per-thousand (e.g. 4.14)"
    )

    @field_validator("tarifa_ica_por_mil", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class LiquidacionICA(BaseModel):
    impuesto_ica: Optional[Decimal] = Field(None)
    impuesto_avisos_tableros: Optional[Decimal] = Field(
        None, description="Usually 15% of ICA"
    )
    sobretasa_bomberil: Optional[Decimal] = Field(None)
    total_impuesto_cargo: Optional[Decimal] = Field(None)
    menos_retenciones_practicadas: Optional[Decimal] = Field(None)
    menos_autorretenciones: Optional[Decimal] = Field(None)
    menos_anticipo_periodo_anterior: Optional[Decimal] = Field(None)
    mas_anticipo_periodo_siguiente: Optional[Decimal] = Field(None)
    sanciones: Optional[Decimal] = Field(None)
    intereses_mora: Optional[Decimal] = Field(None)
    total_a_pagar: Optional[Decimal] = Field(None)
    total_saldo_a_favor: Optional[Decimal] = Field(None)

    @field_validator(
        "impuesto_ica",
        "impuesto_avisos_tableros",
        "sobretasa_bomberil",
        "total_impuesto_cargo",
        "menos_retenciones_practicadas",
        "menos_autorretenciones",
        "menos_anticipo_periodo_anterior",
        "mas_anticipo_periodo_siguiente",
        "sanciones",
        "intereses_mora",
        "total_a_pagar",
        "total_saldo_a_favor",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class DeclaracionICAContent(ContentBase):
    """ICA municipal tax declaration."""

    municipio: Optional[str] = Field(None)
    departamento: Optional[str] = Field(None)
    anio: Optional[int] = Field(None)
    periodicidad: Optional[str] = Field(None, description="anual | bimestral | mensual")
    periodo_numero: Optional[int] = Field(
        None, description="Bimester 1-6 or month 1-12"
    )
    nit_declarante: Optional[str] = Field(None)
    razon_social: Optional[str] = Field(None)
    actividades_economicas: Optional[List[ActividadEconomicaICA]] = Field(None)
    ingresos_brutos: Optional[Decimal] = Field(None)
    total_ingresos_gravables: Optional[Decimal] = Field(None)
    liquidacion: Optional[LiquidacionICA] = Field(None)
    tipo_declaracion: Optional[str] = Field(None, description="inicial | correccion")
    fecha_presentacion: Optional[str] = Field(None, description="YYYY-MM-DD")
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator("ingresos_brutos", "total_ingresos_gravables", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 8. AutoretencionICAContent — autorretencion_ica
# ---------------------------------------------------------------------------


class DetalleAutorretencion(BaseModel):
    actividad_economica: Optional[str] = Field(None)
    codigo_ciiu: Optional[str] = Field(None)
    tarifa_retencion_por_mil: Optional[Decimal] = Field(None)
    base_gravable: Optional[Decimal] = Field(None)
    valor_autorretencion: Optional[Decimal] = Field(None)

    @field_validator(
        "tarifa_retencion_por_mil",
        "base_gravable",
        "valor_autorretencion",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class AutoretencionICAContent(ContentBase):
    """ICA self-withholding declaration."""

    municipio: Optional[str] = Field(None)
    departamento: Optional[str] = Field(None)
    anio: Optional[int] = Field(None)
    periodicidad: Optional[str] = Field(None, description="mensual | bimestral")
    periodo_numero: Optional[int] = Field(None)
    nit_declarante: Optional[str] = Field(None)
    razon_social: Optional[str] = Field(None)
    detalle_autorretenciones: Optional[List[DetalleAutorretencion]] = Field(None)
    total_autorretenciones: Optional[Decimal] = Field(None)
    sanciones: Optional[Decimal] = Field(None)
    intereses_mora: Optional[Decimal] = Field(None)
    total_a_pagar: Optional[Decimal] = Field(None)
    tipo_declaracion: Optional[str] = Field(None, description="inicial | correccion")
    fecha_presentacion: Optional[str] = Field(None, description="YYYY-MM-DD")
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "total_autorretenciones",
        "sanciones",
        "intereses_mora",
        "total_a_pagar",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 9. AnexoIVAContent — anexo_iva
# ---------------------------------------------------------------------------


class IVAGeneradoPorTarifa(BaseModel):
    tarifa: Optional[Decimal] = Field(
        None, description="Rate as decimal (0.19, 0.05, 0.00)"
    )
    base_gravable: Optional[Decimal] = Field(None)
    iva_generado: Optional[Decimal] = Field(None)

    @field_validator("tarifa", "base_gravable", "iva_generado", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class IVADescontablePorConcepto(BaseModel):
    concepto: Optional[str] = Field(
        None,
        description="compras_gravadas | importaciones | servicios | honorarios | otro",
    )
    tarifa: Optional[Decimal] = Field(None)
    base_gravable: Optional[Decimal] = Field(None)
    iva_descontable: Optional[Decimal] = Field(None)

    @field_validator("tarifa", "base_gravable", "iva_descontable", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class AnexoIVAContent(ContentBase):
    """IVA declaration annex — details of generated and discountable IVA."""

    nit_declarante: Optional[str] = Field(None)
    razon_social: Optional[str] = Field(None)
    anio: Optional[int] = Field(None)
    periodicidad: Optional[str] = Field(
        None, description="bimestral | cuatrimestral | anual"
    )
    periodo_numero: Optional[int] = Field(None)
    iva_generado: Optional[List[IVAGeneradoPorTarifa]] = Field(None)
    total_iva_generado: Optional[Decimal] = Field(None)
    iva_descontable: Optional[List[IVADescontablePorConcepto]] = Field(None)
    total_iva_descontable: Optional[Decimal] = Field(None)
    saldo_a_pagar: Optional[Decimal] = Field(None)
    saldo_a_favor: Optional[Decimal] = Field(None)
    retenciones_iva_practicadas: Optional[Decimal] = Field(None)
    retenciones_iva_que_le_practicaron: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "total_iva_generado",
        "total_iva_descontable",
        "saldo_a_pagar",
        "saldo_a_favor",
        "retenciones_iva_practicadas",
        "retenciones_iva_que_le_practicaron",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 10. AuxiliarIVAContent — auxiliar_iva
# ---------------------------------------------------------------------------


class CuentaIVA(BaseModel):
    codigo_cuenta: Optional[str] = Field(None, description="e.g. 2408 or 240810")
    nombre_cuenta: Optional[str] = Field(None)
    tipo_iva: Optional[str] = Field(
        None, description="generado | descontable | por_pagar | retenido"
    )
    saldo_inicial: Optional[Decimal] = Field(None)
    movimientos: Optional[List[Dict[str, Any]]] = Field(None)
    total_debitos: Optional[Decimal] = Field(None)
    total_creditos: Optional[Decimal] = Field(None)
    saldo_final: Optional[Decimal] = Field(None)

    @field_validator(
        "saldo_inicial", "total_debitos", "total_creditos", "saldo_final", mode="before"
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class AuxiliarIVAContent(ContentBase):
    """Auxiliary ledger of IVA accounts."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    cuentas: Optional[List[CuentaIVA]] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )


# ---------------------------------------------------------------------------
# 11. AuxiliaryLedgerContent — libro_auxiliar, auxiliar_impuesto
# ---------------------------------------------------------------------------


class LedgerLine(BaseModel):
    fecha: str = Field(description="Date YYYY-MM-DD")
    cuenta_puc: Optional[str] = Field(None, description="PUC account code")
    cuenta_nombre: Optional[str] = Field(None)
    tercero_nit: Optional[str] = Field(None)
    tercero_nombre: Optional[str] = Field(None)
    comprobante: Optional[str] = Field(None, description="Voucher type and number")
    centro_costo: Optional[str] = Field(None)
    detalle: Optional[str] = Field(None, description="Line detail/description")
    debito: Decimal = Field(ge=0)
    credito: Decimal = Field(ge=0)
    saldo: Optional[Decimal] = Field(None)

    @field_validator("debito", "credito", "saldo", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class AuxiliaryLedgerContent(ContentBase):
    """Content schema for general auxiliary ledgers."""

    entidad: Optional[NitEntidad] = Field(None)
    cuenta_principal: Optional[str] = Field(
        None, description="Main account code if specific to one account"
    )
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo: Optional[str] = Field(None, description="Period label (backward compat)")
    saldo_inicial: Optional[Decimal] = Field(None)
    lines: List[LedgerLine] = Field(description="Ledger lines")
    total_debitos: Optional[Decimal] = Field(None)
    total_creditos: Optional[Decimal] = Field(None)
    saldo_final: Optional[Decimal] = Field(None)
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "saldo_inicial", "total_debitos", "total_creditos", "saldo_final", mode="before"
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 12. FinancialStatementContent — balance_general, estado_resultados (Vía B)
# ---------------------------------------------------------------------------


class AccountBalance(BaseModel):
    cuenta_puc: str = Field(description="PUC account code")
    nombre: str = Field(description="Account name")
    saldo: Decimal = Field(description="Account balance")
    nivel: Optional[int] = Field(
        None, description="Hierarchy level: 1=class, 2=group, 3=account, 4=subaccount"
    )

    @field_validator("saldo", mode="before")
    @classmethod
    def parse_saldo(cls, v):
        return _parse_decimal(v)


class ActivosCorrientes(BaseModel):
    efectivo_equivalentes: Optional[Decimal] = Field(None)
    inversiones_corto_plazo: Optional[Decimal] = Field(None)
    cuentas_por_cobrar_comerciales: Optional[Decimal] = Field(None)
    inventarios: Optional[Decimal] = Field(None)
    activos_por_impuestos_corrientes: Optional[Decimal] = Field(None)
    otros_activos_corrientes: Optional[Decimal] = Field(None)
    total_activos_corrientes: Optional[Decimal] = Field(None)

    @field_validator("*", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class ActivosNoCorrientes(BaseModel):
    propiedades_planta_equipo: Optional[Decimal] = Field(None)
    intangibles: Optional[Decimal] = Field(None)
    activos_por_impuestos_diferidos: Optional[Decimal] = Field(None)
    inversiones_largo_plazo: Optional[Decimal] = Field(None)
    otros_activos_no_corrientes: Optional[Decimal] = Field(None)
    total_activos_no_corrientes: Optional[Decimal] = Field(None)

    @field_validator("*", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class PasivosCorrientes(BaseModel):
    obligaciones_financieras_cp: Optional[Decimal] = Field(None)
    cuentas_por_pagar_comerciales: Optional[Decimal] = Field(None)
    impuestos_por_pagar: Optional[Decimal] = Field(None)
    obligaciones_laborales: Optional[Decimal] = Field(None)
    otros_pasivos_corrientes: Optional[Decimal] = Field(None)
    total_pasivos_corrientes: Optional[Decimal] = Field(None)

    @field_validator("*", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class PasivosNoCorrientes(BaseModel):
    obligaciones_financieras_lp: Optional[Decimal] = Field(None)
    pasivos_por_impuestos_diferidos: Optional[Decimal] = Field(None)
    otros_pasivos_no_corrientes: Optional[Decimal] = Field(None)
    total_pasivos_no_corrientes: Optional[Decimal] = Field(None)

    @field_validator("*", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class Patrimonio(BaseModel):
    capital_social: Optional[Decimal] = Field(None)
    reservas: Optional[Decimal] = Field(None)
    resultados_del_ejercicio: Optional[Decimal] = Field(None)
    resultados_acumulados: Optional[Decimal] = Field(None)
    otro_resultado_integral: Optional[Decimal] = Field(None)
    total_patrimonio: Optional[Decimal] = Field(None)

    @field_validator("*", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class FinancialStatementContent(ContentBase):
    """Content schema for existing financial statements (Vía B)."""

    tipo: Literal["balance_general", "estado_resultados"] = Field(
        description="Statement type"
    )
    entidad: Optional[NitEntidad] = Field(None)
    entity_nit: Optional[str] = Field(None, description="Entity NIT (backward compat)")
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    marco_normativo: Optional[str] = Field(
        None, description="NIIF_plenas | NIIF_pymes | NIF_microempresas"
    )
    # Balance general sections
    activos_corrientes: Optional[ActivosCorrientes] = Field(None)
    activos_no_corrientes: Optional[ActivosNoCorrientes] = Field(None)
    pasivos_corrientes: Optional[PasivosCorrientes] = Field(None)
    pasivos_no_corrientes: Optional[PasivosNoCorrientes] = Field(None)
    patrimonio: Optional[Patrimonio] = Field(None)
    total_activos: Optional[Decimal] = Field(None)
    total_pasivos: Optional[Decimal] = Field(None)
    total_patrimonio: Optional[Decimal] = Field(None)
    verificacion_ecuacion: Optional[bool] = Field(
        None, description="True if activos == pasivos + patrimonio"
    )
    # Estado de resultados sections
    ingresos_ordinarios: Optional[Decimal] = Field(None)
    otros_ingresos: Optional[Decimal] = Field(None)
    total_ingresos: Optional[Decimal] = Field(None)
    costo_ventas: Optional[Decimal] = Field(None)
    utilidad_bruta: Optional[Decimal] = Field(None)
    gastos_administracion: Optional[Decimal] = Field(None)
    gastos_venta: Optional[Decimal] = Field(None)
    total_gastos_operacionales: Optional[Decimal] = Field(None)
    utilidad_operacional: Optional[Decimal] = Field(None)
    ingresos_financieros: Optional[Decimal] = Field(None)
    gastos_financieros: Optional[Decimal] = Field(None)
    utilidad_antes_impuestos: Optional[Decimal] = Field(None)
    impuesto_renta: Optional[Decimal] = Field(None)
    utilidad_neta: Optional[Decimal] = Field(None)
    # Flat account list (backward compat + detailed view)
    accounts: Optional[List[AccountBalance]] = Field(
        None, description="All PUC accounts with balances"
    )
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "total_activos",
        "total_pasivos",
        "total_patrimonio",
        "ingresos_ordinarios",
        "otros_ingresos",
        "total_ingresos",
        "costo_ventas",
        "utilidad_bruta",
        "gastos_administracion",
        "gastos_venta",
        "total_gastos_operacionales",
        "utilidad_operacional",
        "ingresos_financieros",
        "gastos_financieros",
        "utilidad_antes_impuestos",
        "impuesto_renta",
        "utilidad_neta",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 12b. BalanceGeneralContent — focused schema for balance_general
# ---------------------------------------------------------------------------


class BalanceGeneralContent(ContentBase):
    """Balance general / Estado de situación financiera."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_fin: Optional[str] = Field(None, description="Cut-off date YYYY-MM-DD")
    marco_normativo: Optional[str] = Field(
        None, description="NIIF_plenas | NIIF_pymes | NIF_microempresas"
    )
    activos_corrientes: Optional[ActivosCorrientes] = Field(None)
    activos_no_corrientes: Optional[ActivosNoCorrientes] = Field(None)
    pasivos_corrientes: Optional[PasivosCorrientes] = Field(None)
    pasivos_no_corrientes: Optional[PasivosNoCorrientes] = Field(None)
    patrimonio: Optional[Patrimonio] = Field(None)
    total_activos: Optional[Decimal] = Field(None)
    total_pasivos: Optional[Decimal] = Field(None)
    total_patrimonio: Optional[Decimal] = Field(None)
    verificacion_ecuacion: Optional[bool] = Field(
        None, description="True if activos == pasivos + patrimonio"
    )
    accounts: Optional[List[AccountBalance]] = Field(
        None,
        description=(
            "Flat list of UNIQUE PUC accounts with balances. "
            "Include each cuenta_puc EXACTLY ONCE. Maximum 300 entries."
        ),
    )
    informacion_adicional: Optional[Dict[str, Any]] = Field(None)

    @field_validator(
        "total_activos",
        "total_pasivos",
        "total_patrimonio",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)

    @model_validator(mode="after")
    def deduplicate_accounts(self) -> "BalanceGeneralContent":
        if self.accounts:
            seen: dict[str, AccountBalance] = {}
            for acct in self.accounts:
                key = (acct.cuenta_puc or "").strip()
                if key not in seen:
                    seen[key] = acct
            self.accounts = list(seen.values())[:300]
        return self


# ---------------------------------------------------------------------------
# 12c. EstadoResultadosContent — focused schema for estado_resultados
# ---------------------------------------------------------------------------


class EstadoResultadosContent(ContentBase):
    """Estado de resultados / Estado de pérdidas y ganancias."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    marco_normativo: Optional[str] = Field(
        None, description="NIIF_plenas | NIIF_pymes | NIF_microempresas"
    )
    ingresos_ordinarios: Optional[Decimal] = Field(None)
    otros_ingresos: Optional[Decimal] = Field(None)
    total_ingresos: Optional[Decimal] = Field(None)
    costo_ventas: Optional[Decimal] = Field(None)
    utilidad_bruta: Optional[Decimal] = Field(None)
    gastos_administracion: Optional[Decimal] = Field(None)
    gastos_venta: Optional[Decimal] = Field(None)
    total_gastos_operacionales: Optional[Decimal] = Field(None)
    utilidad_operacional: Optional[Decimal] = Field(None)
    ingresos_financieros: Optional[Decimal] = Field(None)
    gastos_financieros: Optional[Decimal] = Field(None)
    utilidad_antes_impuestos: Optional[Decimal] = Field(None)
    impuesto_renta: Optional[Decimal] = Field(None)
    utilidad_neta: Optional[Decimal] = Field(None)
    accounts: Optional[List[AccountBalance]] = Field(
        None, description="Flat list of all PUC accounts (class 4, 5, 6)"
    )
    informacion_adicional: Optional[Dict[str, Any]] = Field(None)

    @field_validator(
        "ingresos_ordinarios",
        "otros_ingresos",
        "total_ingresos",
        "costo_ventas",
        "utilidad_bruta",
        "gastos_administracion",
        "gastos_venta",
        "total_gastos_operacionales",
        "utilidad_operacional",
        "ingresos_financieros",
        "gastos_financieros",
        "utilidad_antes_impuestos",
        "impuesto_renta",
        "utilidad_neta",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 13. LibroDiarioContent — libro_diario
# ---------------------------------------------------------------------------


class LibroDiarioContent(ContentBase):
    """Official daily journal — chronological record of all accounting vouchers."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    asientos: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Journal entries with date, comprobante, accounts, debits, credits",
    )
    total_debitos: Optional[Decimal] = Field(None)
    total_creditos: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator("total_debitos", "total_creditos", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 14. FlujoDeCajaContent — flujo_de_caja
# ---------------------------------------------------------------------------


class FlujoDeCajaContent(ContentBase):
    """Cash flow statement (NIC 7 / Section 7 NIIF Pymes)."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    metodo: Optional[str] = Field(None, description="directo | indirecto")
    flujo_neto_operacion: Optional[Decimal] = Field(None)
    detalle_operacion: Optional[List[Dict[str, Any]]] = Field(None)
    flujo_neto_inversion: Optional[Decimal] = Field(None)
    detalle_inversion: Optional[List[Dict[str, Any]]] = Field(None)
    flujo_neto_financiacion: Optional[Decimal] = Field(None)
    detalle_financiacion: Optional[List[Dict[str, Any]]] = Field(None)
    aumento_disminucion_neto: Optional[Decimal] = Field(None)
    efectivo_inicio_periodo: Optional[Decimal] = Field(None)
    efectivo_fin_periodo: Optional[Decimal] = Field(None)
    verificacion: Optional[bool] = Field(
        None, description="True if efectivo_inicio + variacion == efectivo_fin"
    )
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "flujo_neto_operacion",
        "flujo_neto_inversion",
        "flujo_neto_financiacion",
        "aumento_disminucion_neto",
        "efectivo_inicio_periodo",
        "efectivo_fin_periodo",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 15. CambiosPatrimonioContent — cambios_patrimonio
# ---------------------------------------------------------------------------


class ComponentePatrimonio(BaseModel):
    concepto_patrimonio: Optional[str] = Field(
        None,
        description="capital_social | prima_emision | reservas | resultados_acumulados | resultado_ejercicio | ORI | otro",
    )
    saldo_inicial: Optional[Decimal] = Field(None)
    movimientos: Optional[List[Dict[str, Any]]] = Field(None)
    saldo_final: Optional[Decimal] = Field(None)

    @field_validator("saldo_inicial", "saldo_final", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class CambiosPatrimonioContent(ContentBase):
    """Statement of changes in equity (NIC 1 / Section 6 NIIF Pymes)."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    componentes: Optional[List[ComponentePatrimonio]] = Field(None)
    total_patrimonio_inicio: Optional[Decimal] = Field(None)
    total_patrimonio_fin: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator("total_patrimonio_inicio", "total_patrimonio_fin", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 16. NotasEstadosFinancierosContent — notas_estados_financieros
# ---------------------------------------------------------------------------


class NotaFinanciera(BaseModel):
    numero_nota: Optional[str] = Field(None)
    titulo: Optional[str] = Field(None)
    categoria: Optional[str] = Field(
        None,
        description="politicas_contables | estimaciones_juicios | detalle_partida | contingencias | hechos_posteriores | partes_relacionadas | impuestos | otra",
    )
    contenido_resumido: Optional[str] = Field(
        None, description="Summary of the note key content (max 500 words)"
    )
    cifras_relevantes: Optional[List[Dict[str, Any]]] = Field(None)
    politica_contable_descrita: Optional[str] = Field(None)


class NotasEstadosFinancierosContent(ContentBase):
    """Notes to financial statements (NIC 1 / Section 8 NIIF Pymes)."""

    entidad: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    notas: Optional[List[NotaFinanciera]] = Field(None)
    moneda_funcional: Optional[str] = Field(None)
    base_presentacion: Optional[str] = Field(
        None, description="NIIF_plenas | NIIF_pymes | NIF_microempresas"
    )
    hipotesis_negocio_en_marcha: Optional[bool] = Field(None)
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )


# ---------------------------------------------------------------------------
# 17. ComprobanteEgresoContent — comprobante_egreso
# ---------------------------------------------------------------------------


class ComprobanteEgresoContent(ContentBase):
    """Payment voucher — records cash/bank outflows."""

    numero_comprobante: Optional[str] = Field(None, description="Voucher number")
    fecha: Optional[str] = Field(None, description="YYYY-MM-DD")
    beneficiario: Optional[NitEntidad] = Field(None, description="Payment recipient")
    concepto: Optional[str] = Field(None, description="Payment concept/description")
    valor_bruto: Optional[Decimal] = Field(None)
    retenciones: Optional[List[Retencion]] = Field(None)
    valor_neto: Optional[Decimal] = Field(
        None, description="Net payment after retentions"
    )
    forma_pago: Optional[str] = Field(
        None, description="efectivo | cheque | transferencia | otro"
    )
    banco: Optional[str] = Field(None)
    numero_cheque: Optional[str] = Field(None)
    cuenta_debitar: Optional[str] = Field(None, description="PUC account to debit")
    aprobado_por: Optional[str] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None,
        description="Any other data relevant for downstream accounting: PUC classification clues, cost centers, references, authorizations.",
    )

    @field_validator("valor_bruto", "valor_neto", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 18. DocumentoSoporteContent — documento_soporte
# ---------------------------------------------------------------------------


class DocumentoSoporteContent(ContentBase):
    """Support document for purchases from parties not required to invoice (DUR 1625/2016 art. 1.6.1.4.12)."""

    numero_documento: Optional[str] = Field(None)
    fecha_emision: Optional[str] = Field(None, description="YYYY-MM-DD")
    proveedor: Optional[EmisorFactura] = Field(
        None, description="Non-invoicing supplier"
    )
    empresa_adquirente: Optional[ReceptorFactura] = Field(None)
    descripcion_servicio: Optional[str] = Field(None)
    items: Optional[List[FacturaItem]] = Field(None)
    totales: Optional[FacturaTotales] = Field(None)
    retenciones_aplicadas: Optional[List[Retencion]] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )


# ---------------------------------------------------------------------------
# 19. ReciboCajaContent — recibo_caja
# ---------------------------------------------------------------------------


class ReciboCajaContent(ContentBase):
    """Cash receipt — records cash inflows."""

    numero_recibo: Optional[str] = Field(None)
    fecha: Optional[str] = Field(None, description="YYYY-MM-DD")
    recibido_de: Optional[NitEntidad] = Field(None, description="Payer")
    concepto: Optional[str] = Field(None)
    valor: Optional[Decimal] = Field(None)
    forma_pago: Optional[str] = Field(
        None, description="efectivo | cheque | transferencia | otro"
    )
    banco: Optional[str] = Field(None)
    numero_cheque: Optional[str] = Field(None)
    cuenta_acreditar: Optional[str] = Field(None, description="PUC account to credit")
    elaborado_por: Optional[str] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator("valor", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 20. NominaContent — nomina
# ---------------------------------------------------------------------------


class EmpleadoNomina(BaseModel):
    nombre: Optional[str] = Field(None)
    cedula: Optional[str] = Field(None)
    cargo: Optional[str] = Field(None)
    salario_basico: Optional[Decimal] = Field(None)
    dias_trabajados: Optional[int] = Field(None)
    devengado_total: Optional[Decimal] = Field(None)
    deduccion_salud: Optional[Decimal] = Field(None)
    deduccion_pension: Optional[Decimal] = Field(None)
    deduccion_retefuente: Optional[Decimal] = Field(None)
    otras_deducciones: Optional[Decimal] = Field(None)
    total_deducciones: Optional[Decimal] = Field(None)
    neto_pagar: Optional[Decimal] = Field(None)

    @field_validator(
        "salario_basico",
        "devengado_total",
        "deduccion_salud",
        "deduccion_pension",
        "deduccion_retefuente",
        "otras_deducciones",
        "total_deducciones",
        "neto_pagar",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class NominaContent(ContentBase):
    """Payroll document."""

    empresa: Optional[NitEntidad] = Field(None)
    periodo_inicio: Optional[str] = Field(None, description="YYYY-MM-DD")
    periodo_fin: Optional[str] = Field(None, description="YYYY-MM-DD")
    empleados: Optional[List[EmpleadoNomina]] = Field(None)
    total_devengado: Optional[Decimal] = Field(None)
    total_deducciones: Optional[Decimal] = Field(None)
    total_neto_pagar: Optional[Decimal] = Field(None)
    aportes_patronales: Optional[Dict[str, Any]] = Field(
        None,
        description="Employer contributions: salud, pension, ARL, SENA, ICBF, caja",
    )
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None,
        description="Any other data relevant for downstream accounting: PUC accounts for payroll entries, labor law references, cost centers.",
    )

    @field_validator(
        "total_devengado", "total_deducciones", "total_neto_pagar", mode="before"
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 21. ConciliacionBancariaContent — conciliacion_bancaria
# ---------------------------------------------------------------------------


class PartidaConciliatoria(BaseModel):
    descripcion: Optional[str] = Field(None)
    fecha: Optional[str] = Field(None, description="YYYY-MM-DD")
    tipo: Optional[str] = Field(
        None,
        description="cheque_en_transito | deposito_en_transito | nota_debito | nota_credito | error | otro",
    )
    valor: Optional[Decimal] = Field(None)

    @field_validator("valor", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class ConciliacionBancariaContent(ContentBase):
    """Bank reconciliation — reconciles book balance with bank statement balance."""

    empresa: Optional[NitEntidad] = Field(None)
    entidad_financiera: Optional[str] = Field(None)
    numero_cuenta: Optional[str] = Field(None)
    fecha_corte: Optional[str] = Field(None, description="YYYY-MM-DD")
    saldo_segun_extracto: Optional[Decimal] = Field(None)
    saldo_segun_libros: Optional[Decimal] = Field(None)
    partidas_conciliatorias: Optional[List[PartidaConciliatoria]] = Field(None)
    saldo_conciliado: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "saldo_segun_extracto", "saldo_segun_libros", "saldo_conciliado", mode="before"
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 22. CuentaCobroContent — cuenta_cobro
# ---------------------------------------------------------------------------


class CuentaCobroContent(ContentBase):
    """Cuenta de cobro — informal billing document by non-invoicing natural persons."""

    numero: Optional[str] = Field(None)
    fecha: Optional[str] = Field(None, description="YYYY-MM-DD")
    prestador: Optional[NitEntidad] = Field(None, description="Service provider")
    contratante: Optional[NitEntidad] = Field(None, description="Client company")
    concepto: Optional[str] = Field(None, description="Service description")
    valor: Optional[Decimal] = Field(None)
    retenciones: Optional[List[Retencion]] = Field(None)
    valor_neto: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator("valor", "valor_neto", mode="before")
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 23. PlanillaSegSocialContent — planilla_seguridad_social
# ---------------------------------------------------------------------------


class AporteEmpleado(BaseModel):
    nombre: Optional[str] = Field(None)
    cedula: Optional[str] = Field(None)
    salario_base: Optional[Decimal] = Field(None)
    aporte_salud_empleado: Optional[Decimal] = Field(None)
    aporte_salud_empleador: Optional[Decimal] = Field(None)
    aporte_pension_empleado: Optional[Decimal] = Field(None)
    aporte_pension_empleador: Optional[Decimal] = Field(None)
    aporte_arl: Optional[Decimal] = Field(None)
    aporte_caja: Optional[Decimal] = Field(None)

    @field_validator(
        "salario_base",
        "aporte_salud_empleado",
        "aporte_salud_empleador",
        "aporte_pension_empleado",
        "aporte_pension_empleador",
        "aporte_arl",
        "aporte_caja",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


class PlanillaSegSocialContent(ContentBase):
    """Social security contributions form (PILA)."""

    empresa: Optional[NitEntidad] = Field(None)
    periodo: Optional[str] = Field(None, description="YYYY-MM")
    numero_planilla: Optional[str] = Field(None)
    empleados: Optional[List[AporteEmpleado]] = Field(None)
    total_salud: Optional[Decimal] = Field(None)
    total_pension: Optional[Decimal] = Field(None)
    total_arl: Optional[Decimal] = Field(None)
    total_caja: Optional[Decimal] = Field(None)
    total_parafiscales: Optional[Decimal] = Field(None)
    total_a_pagar: Optional[Decimal] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "total_salud",
        "total_pension",
        "total_arl",
        "total_caja",
        "total_parafiscales",
        "total_a_pagar",
        mode="before",
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# 24. ReciboPagoImpuestoContent — recibo_pago_impuesto
# ---------------------------------------------------------------------------


class ReciboPagoImpuestoContent(ContentBase):
    """Tax payment receipt — proof of payment to fiscal authority."""

    numero_recibo: Optional[str] = Field(None)
    fecha_pago: Optional[str] = Field(None, description="YYYY-MM-DD")
    tipo_impuesto: Optional[str] = Field(
        None, description="IVA | renta | ICA | GMF | retefuente | reteica | otro"
    )
    entidad_fiscal: Optional[str] = Field(None, description="DIAN or municipal entity")
    nit_declarante: Optional[str] = Field(None)
    razon_social: Optional[str] = Field(None)
    periodo_gravable: Optional[str] = Field(None, description="Period paid for")
    valor_principal: Optional[Decimal] = Field(None)
    sanciones: Optional[Decimal] = Field(None)
    intereses: Optional[Decimal] = Field(None)
    total_pagado: Optional[Decimal] = Field(None)
    banco: Optional[str] = Field(None)
    referencia_pago: Optional[str] = Field(None)
    moneda: Optional[str] = Field("COP")
    informacion_adicional: Optional[Dict[str, Any]] = Field(
        None, description="Any other data relevant for downstream accounting."
    )

    @field_validator(
        "valor_principal", "sanciones", "intereses", "total_pagado", mode="before"
    )
    @classmethod
    def parse_amounts(cls, v):
        return _parse_decimal(v)


# ---------------------------------------------------------------------------
# Schema registry: maps DocumentType values to content schemas
# ---------------------------------------------------------------------------

from app.models.document_types import DocumentType  # noqa: E402

INGEST_CONTENT_SCHEMAS: dict[str, type[BaseModel]] = {
    # Original Vía A types (with upgraded schemas)
    DocumentType.FACTURA_VENTA.value: FacturaVentaContent,
    DocumentType.FACTURA_COMPRA.value: FacturaCompraContent,
    DocumentType.NOTA_CREDITO.value: NotaCreditoContent,
    DocumentType.NOTA_DEBITO.value: NotaDebitoContent,
    DocumentType.EXTRACTO_BANCARIO.value: BankStatementContent,
    DocumentType.DECLARACION_IVA.value: TaxDeclarationContent,
    DocumentType.DECLARACION_RETEICA.value: TaxDeclarationContent,
    DocumentType.ANEXO_TRIBUTARIO.value: AnexoIVAContent,
    DocumentType.AUXILIAR_IMPUESTO.value: AuxiliaryLedgerContent,
    # New Vía A types
    DocumentType.DECLARACION_ICA.value: DeclaracionICAContent,
    DocumentType.AUTORRETENCION_ICA.value: AutoretencionICAContent,
    DocumentType.ANEXO_IVA.value: AnexoIVAContent,
    DocumentType.AUXILIAR_IVA.value: AuxiliarIVAContent,
    DocumentType.COMPROBANTE_EGRESO.value: ComprobanteEgresoContent,
    DocumentType.DOCUMENTO_SOPORTE.value: DocumentoSoporteContent,
    DocumentType.RECIBO_CAJA.value: ReciboCajaContent,
    DocumentType.NOMINA.value: NominaContent,
    DocumentType.CONCILIACION_BANCARIA.value: ConciliacionBancariaContent,
    DocumentType.CUENTA_COBRO.value: CuentaCobroContent,
    DocumentType.PLANILLA_SEGURIDAD_SOCIAL.value: PlanillaSegSocialContent,
    DocumentType.RECIBO_PAGO_IMPUESTO.value: ReciboPagoImpuestoContent,
    # Original Vía B types (with upgraded schemas)
    DocumentType.BALANCE_GENERAL.value: BalanceGeneralContent,
    DocumentType.ESTADO_RESULTADOS.value: EstadoResultadosContent,
    DocumentType.LIBRO_AUXILIAR.value: AuxiliaryLedgerContent,
    # New Vía B types
    DocumentType.LIBRO_DIARIO.value: LibroDiarioContent,
    DocumentType.FLUJO_DE_CAJA.value: FlujoDeCajaContent,
    DocumentType.CAMBIOS_PATRIMONIO.value: CambiosPatrimonioContent,
    DocumentType.NOTAS_ESTADOS_FINANCIEROS.value: NotasEstadosFinancierosContent,
}
