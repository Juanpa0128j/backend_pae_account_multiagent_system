"""
Pydantic structured-output schemas for LLM responses.

These models are used by LLMClient to parse structured JSON from the LLM.
They are separate from ingest_schemas (raw document extraction) and database
models (ORM). Import from here, not from app.core.gemini_client.
"""

from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Ingest / transaction extraction
# ---------------------------------------------------------------------------


class RawTransaction(BaseModel):
    """Structured schema for extracted receipt/invoice data."""

    fecha: Optional[str] = Field(None, description="Date in YYYY-MM-DD format")
    nit_emisor: str = Field(description="NIT of the issuer")
    nit_receptor: str = Field(description="NIT of the receiver (empresa)")
    total: Decimal = Field(description="Total amount of the transaction")
    descripcion: Optional[str] = Field(
        None, description="Description/concept of the transaction"
    )
    items: Optional[List[Dict[str, Any]]] = Field(None, description="Line items")

    @field_validator("total", mode="before")
    @classmethod
    def parse_total(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class RawTransactionsList(BaseModel):
    transactions: List[RawTransaction] = Field(
        default_factory=list,
        description="Extracted list of transactions from the document",
    )


# ---------------------------------------------------------------------------
# Contador (journal entry) schemas
# ---------------------------------------------------------------------------


class AsientoContable(BaseModel):
    """Simplified journal entry schema for structured output."""

    cuenta_puc: str = Field(description="PUC account code (1-6 digits)")
    descripcion: Optional[str] = Field(
        default=None, description="Description of the entry"
    )
    tipo_movimiento: Literal["debito", "credito"] = Field(
        description="Movement type: 'debito' or 'credito' (lowercase)"
    )
    valor: Decimal = Field(description="Amount of the entry")

    @field_validator("valor", mode="before")
    @classmethod
    def parse_valor(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class ContadorOutput(BaseModel):
    """ContadorOutput-compatible schema for structured output."""

    fecha_registro: str = Field(description="Accounting registration date YYYY-MM-DD")
    tipo_documento: str = Field(
        description=(
            "Document type: recibo, factura, extracto, nota_credito, "
            "nota_debito, comprobante_egreso, otro"
        )
    )
    descripcion_general: str = Field(
        description="General description of the accounting event"
    )
    asientos: List[AsientoContable] = Field(
        description="Journal entries (at least one debit and one credit)"
    )
    total_debitos: Decimal = Field(
        default=Decimal("0"), description="Sum of all debit entries"
    )
    total_creditos: Decimal = Field(
        default=Decimal("0"), description="Sum of all credit entries"
    )

    @field_validator("total_debitos", "total_creditos", mode="before")
    @classmethod
    def parse_totals(cls, v):  # noqa: N805
        if v is None:
            return Decimal("0")
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v

    @model_validator(mode="after")
    def ensure_totals(self) -> "ContadorOutput":
        """Backfill totals when LLM omits them but asientos are present."""
        if not self.asientos:
            return self

        calc_debitos = Decimal("0")
        calc_creditos = Decimal("0")
        for asiento in self.asientos:
            valor = Decimal(str(asiento.valor or 0))
            if asiento.tipo_movimiento == "debito":
                calc_debitos += valor
            else:
                calc_creditos += valor

        if self.total_debitos == Decimal("0") and calc_debitos > Decimal("0"):
            self.total_debitos = calc_debitos
        if self.total_creditos == Decimal("0") and calc_creditos > Decimal("0"):
            self.total_creditos = calc_creditos

        return self


# ---------------------------------------------------------------------------
# Auditor schemas
# ---------------------------------------------------------------------------


class AuditorHallazgo(BaseModel):
    """Single audit finding."""

    codigo: str = Field(default="AUD-000", description="Finding code in format AUD-XXX")
    severidad: Literal["info", "advertencia", "error", "critico"] = Field(
        default="advertencia", description="Finding severity level"
    )
    descripcion: str = Field(default="", description="Clear description of the finding")
    campo_afectado: Optional[str] = Field(
        None, description="Campo contable afectado (opcional)"
    )
    recomendacion: str = Field(
        default="", description="Recomendacion para corregir el hallazgo"
    )


class AuditorOutput(BaseModel):
    """AuditorOutput-compatible schema for structured output."""

    fecha_auditoria: str = Field(
        default="1970-01-01", description="Fecha de auditoria en formato YYYY-MM-DD"
    )
    documento_referencia: str = Field(
        default="sin referencia", description="Referencia del documento auditado"
    )
    aprobado: bool = Field(
        default=False, description="True cuando el audit pasa sin bloqueadores"
    )
    nivel_riesgo: Literal["bajo", "medio", "alto", "critico"] = Field(
        default="medio", description="Nivel de riesgo global de la transaccion"
    )
    hallazgos: List[AuditorHallazgo] = Field(
        default_factory=list,
        description="Lista estructurada de hallazgos detectados",
    )
    puntaje_calidad: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=100,
        description="Puntaje de calidad contable entre 0 y 100",
    )
    resumen: str = Field(default="", description="Resumen ejecutivo de la auditoria")


# ---------------------------------------------------------------------------
# Tributario (tax) schemas
# ---------------------------------------------------------------------------


class TaxJustification(BaseModel):
    """Structured output for tax justification calls."""

    referencias: List[str] = Field(
        description="Legal articles cited, e.g. ['Art. 383 ET', 'Decreto 2048/1992']"
    )
    justificacion: str = Field(
        description="Spanish explanation of why these rates apply to the transaction"
    )
    confirma_tasas: bool = Field(
        description="True if the normative context confirms the calculated rates"
    )


class TaxRateLookup(BaseModel):
    """Structured output for tax profile setup."""

    tasa_retefuente_servicios: Decimal = Field(
        description="Retefuente rate for services as decimal fraction, e.g. 0.11"
    )
    tasa_retefuente_bienes: Decimal = Field(
        description="Retefuente rate for goods purchases as decimal fraction"
    )
    tasa_retefuente_arrendamiento: Decimal = Field(
        description="Retefuente rate for lease/rent as decimal fraction"
    )
    tasa_reteica: Decimal = Field(
        description="ReteICA rate for city/CIIU as decimal fraction"
    )
    tasa_iva_general: Decimal = Field(
        description="IVA tariff as decimal fraction (0.19 or 0.0)"
    )
    fuentes: List[str] = Field(
        description="Legal articles and municipal agreements supporting rates"
    )

    @field_validator(
        "tasa_retefuente_servicios",
        "tasa_retefuente_bienes",
        "tasa_retefuente_arrendamiento",
        "tasa_reteica",
        "tasa_iva_general",
        mode="before",
    )
    @classmethod
    def parse_rates(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


# ---------------------------------------------------------------------------
# Reportero analysis schemas
# ---------------------------------------------------------------------------


class ExplicacionResultado(BaseModel):
    """Detailed explanation of a financial metric."""

    metrica: str = Field(
        description="Metric name, e.g. 'activos_totales', 'razon_corriente'"
    )
    valor: float = Field(description="The metric's numeric value")
    explicacion: str = Field(
        description="WHY this value — root causes, contributing accounts, business implications"
    )
    nivel: Literal["positivo", "neutral", "negativo"] = Field(
        description="Traffic light assessment"
    )


class PrediccionPeriodo(BaseModel):
    """Single month financial prediction."""

    periodo: str = Field(description="Target month as YYYY-MM, e.g. '2026-04'")
    ingresos_estimados: float = Field(description="Projected revenue for the month")
    gastos_estimados: float = Field(description="Projected expenses for the month")
    utilidad_estimada: float = Field(description="Projected net profit for the month")
    flujo_caja_estimado: float = Field(
        description="Projected net cash flow for the month (based on historical cash movements)"
    )
    confianza: Literal["alta", "media", "baja"] = Field(
        description="Confidence level based on data volume and trend consistency"
    )


class InterpretacionRatio(BaseModel):
    """Interpretation of a single financial ratio."""

    ratio: str = Field(description="Ratio name in Spanish")
    valor: Optional[float] = Field(None, description="Numeric value")
    interpretacion: str = Field(
        default="",
        description="What this ratio means for the business",
    )
    que_significa: str = Field(
        default="",
        description="Plain-language explanation for non-accountants",
    )


class ReporteroAnalysis(BaseModel):
    """Full structured analysis output from the Reportero LLM call."""

    resumen_ejecutivo: str = Field(
        description="2-3 paragraph executive summary of financial health"
    )
    explicaciones: List[ExplicacionResultado] = Field(
        description="Detailed explanation of EACH major financial result"
    )
    interpretacion_ratios: List[InterpretacionRatio] = Field(
        description="Interpretation of each financial ratio"
    )
    tendencias: str = Field(
        description="Narrative of how revenue, expenses, profit evolved over recent months"
    )
    predicciones: List[PrediccionPeriodo] = Field(
        default_factory=list,
        description="3-month financial projections",
    )

    @field_validator("predicciones", mode="before")
    @classmethod
    def _coerce_predicciones(cls, v):  # noqa: N805
        if isinstance(v, str):
            return []
        return v

    predicciones_narrativa: str = Field(
        description="Plain-language interpretation of predictions: where the company is headed, risks, inflection points"
    )
    alertas: List[str] = Field(description="Risk alerts and early warning signals")
    recomendaciones: List[str] = Field(description="3-5 actionable recommendations")
    nivel_salud_financiera: str = Field(
        description="Overall financial health assessment: bueno, aceptable, preocupante, or critico"
    )

    @field_validator("nivel_salud_financiera", mode="before")
    @classmethod
    def _normalize_salud(cls, v):  # noqa: N805
        import unicodedata

        if isinstance(v, str):
            v = unicodedata.normalize("NFD", v)
            v = "".join(c for c in v if unicodedata.category(c) != "Mn")
            v = v.lower().strip()
        return v


class ReporteroBriefAnalysis(BaseModel):
    """Brief analysis for individual report types (balance, pnl, etc.)."""

    resumen: str = Field(description="1-2 paragraph summary of this specific report")
    puntos_clave: List[str] = Field(description="3-5 key takeaways")
    alertas: List[str] = Field(default_factory=list, description="Risk alerts if any")
    recomendaciones: List[str] = Field(
        default_factory=list, description="1-3 recommendations"
    )


# ---------------------------------------------------------------------------
# Chatbot schemas
# ---------------------------------------------------------------------------


class ChatIntentClassification(BaseModel):
    """LLM structured output for classifying a user's financial question."""

    intent: Literal[
        "balance",
        "pnl",
        "cashflow",
        "iva",
        "withholdings",
        "analysis",
        "top_accounts",
        "ratios",
        "general_question",
        "explanation",
        "dashboard",
    ] = Field(description="Classified intent of the user's question")
    needs_data: bool = Field(
        description="Whether financial data from DB is required to answer"
    )
    rag_query: Optional[str] = Field(
        None,
        description="If RAG normative search would help, the Spanish query to use",
    )
    explanation: str = Field(description="Brief reason for this classification")


class ChatbotResponse(BaseModel):
    """LLM structured output for the non-streaming chat response."""

    respuesta: str = Field(
        description="Conversational response in Spanish, Markdown allowed"
    )
    puntos_clave: List[str] = Field(
        default_factory=list, description="Key points highlighted"
    )
    referencias_normativas: List[str] = Field(
        default_factory=list,
        description="Legal/normative references cited (e.g. Art. 383 ET)",
    )


GENERAL_EXTRACTION_INSTRUCTIONS = """
INSTRUCCIONES GENERALES DE EXTRACCIÓN:
1. Extrae SOLO los campos que estén presentes en el documento. Si un campo no existe, usa null.
2. Fechas: formato ISO 8601 (YYYY-MM-DD). Si solo hay mes/año, usa el último día del mes.
3. NIT: incluir dígito de verificación separado por guion (ej: 900123456-7). Si el DV no aparece, déjalo como el NIT sin DV.
4. Moneda: todos los valores monetarios son numéricos, sin separadores de miles, usando punto como decimal. Moneda por defecto: COP.
5. Tarifas de impuestos: como decimal (ej: 19% → 0.19, 4.14‰ → 0.00414).
6. En el campo `informacion_adicional`, captura TODO lo que pueda ser útil para el procesamiento contable posterior:
   - Conceptos de retención mencionados (retefuente, reteICA, reteIVA)
   - Referencias a resoluciones DIAN, acuerdos municipales, decretos
   - Actividades económicas (códigos CIIU)
   - Régimen tributario del emisor/receptor
   - Centros de costo, proyectos o contratos referenciados
   - Sellos, firmas y autorizaciones presentes
   - Números de contrato, órdenes de compra, referencias cruzadas
   - Cualquier anomalía, dato inusual o información ambigua
   - Observaciones que el contador, tributarista o auditor necesitarían conocer
"""


# ---------------------------------------------------------------------------
# Document classification (used by doc_classifier service)
# ---------------------------------------------------------------------------


class ClassificationResponse(BaseModel):
    """Raw LLM output for document classification."""

    doc_type: str = Field(description="One of the document type values listed")
    confidence: float = Field(
        ge=0, le=1, description="How confident you are in this classification"
    )
    period_start: Optional[str] = Field(
        default=None, description="Start of the period covered, YYYY-MM-DD"
    )
    period_end: Optional[str] = Field(
        default=None, description="End of the period covered, YYYY-MM-DD"
    )
    entity_nit: Optional[str] = Field(default=None, description="NIT of the entity")
    entity_name: Optional[str] = Field(default=None, description="Name of the entity")


CLASSIFICATION_PROMPT = """Eres un experto contable colombiano. Analiza el siguiente contenido extraído de un documento y clasifícalo.

Tipos de documento posibles:

DOCUMENTOS FUENTE (para construir contabilidad desde cero):
- factura_venta: Factura de venta ELECTRÓNICA emitida (con CUFE o código QR DIAN, resolución de facturación). Contiene datos del vendedor, comprador, items/servicios vendidos, subtotal, IVA, total. Obligatoriamente tiene un número consecutivo con prefijo (ej. FV-192, F-001) y cuadra con resolución DIAN.
- factura_compra: Factura de compra recibida de un proveedor obligado a facturar. Similar a factura_venta pero desde la perspectiva del comprador.
- extracto_bancario: Extracto o estado de cuenta bancario. Lista de movimientos con fechas, conceptos, débitos, créditos y saldos.
- nota_credito: Nota crédito comercial. Reduce el valor de una factura previamente emitida.
- nota_debito: Nota débito comercial. Incrementa el valor de una factura previamente emitida.
- declaracion_iva: Declaración bimestral de IVA ante la DIAN. Formulario con renglones numerados, IVA generado vs descontable, saldo a pagar/favor.
- declaracion_reteica: Declaración de retención de ICA o autorretención. Formulario municipal con bases gravables y tarifas por actividad.
- anexo_tributario: Anexo o soporte de una declaración tributaria. Tabla detallada con terceros (NIT, razón social), bases, tarifas, retenciones.
- auxiliar_impuesto: Libro auxiliar de un impuesto específico (ej: auxiliar de IVA). Movimientos contables de cuentas de impuestos con débitos, créditos, saldos.
- declaracion_ica: Declaración de impuesto de industria y comercio (ICA) municipal. Formulario con ingresos brutos, tarifas por actividad económica (CIIU), total a pagar.
- autorretencion_ica: Declaración de autorretención a título de ICA. Periodicidad mensual o bimestral municipal. Contiene bases gravables y valor de autorretención por actividad.
- anexo_iva: Anexo de declaración de IVA. Detalla IVA generado por tarifa y IVA descontable por concepto.
- auxiliar_iva: Libro auxiliar de cuentas de IVA (generado, descontable, por pagar). Movimientos de cuentas 2408xx con débitos, créditos, saldos.
- comprobante_egreso: Comprobante de egreso o pago. Registro de salida de efectivo con beneficiario, concepto, valor, retenciones aplicadas y forma de pago.
- documento_soporte: Documento soporte en adquisiciones a no obligados a facturar (art. 1.6.1.4.12 DUR 1625/2016). Lo emite el COMPRADOR para soportar compras a personas naturales o informales. Señales FUERTES: vendedor con régimen "0-49 No responsable de IVA", texto "Solución Gratuita DIAN", "Representación Gráfica" sin CUFE, prefijo "DS" o "DM" en el número de documento, vendedor es persona natural (cédula, no NIT). Si el vendedor aparece como "No responsable de IVA" o el documento fue "Generado por: Solución Gratuita DIAN", clasifica SIEMPRE como documento_soporte aunque tenga formato de factura.
- recibo_caja: Recibo de caja. Registro de ingreso de efectivo con pagador, concepto, valor y forma de pago.
- nomina: Nómina o liquidación de salarios. Contiene empleados, salarios, deducciones (salud, pensión, retención), prestaciones sociales y neto a pagar.
- conciliacion_bancaria: Conciliación bancaria. Reconcilia saldo en libros con saldo en extracto bancario, listando partidas en tránsito.
- cuenta_cobro: Cuenta de cobro. Documento informal de cobro de servicios por persona natural no obligada a facturar.
- planilla_seguridad_social: Planilla de aportes a seguridad social (PILA). Contiene empleados, salarios base, aportes a salud, pensión, ARL y caja de compensación.
- recibo_pago_impuesto: Recibo de pago de impuesto. Comprobante de pago realizado a una entidad fiscal con tipo de impuesto, período, valor pagado y banco.

ESTADOS FINANCIEROS EXISTENTES (para usar directamente en reportes):
- balance_general: Balance general / Estado de situación financiera. Muestra activos, pasivos y patrimonio con saldos por cuenta PUC.
- estado_resultados: Estado de resultados / PyG. Muestra ingresos, costos, gastos y utilidad neta con cuentas PUC clase 4, 5, 6.
- libro_auxiliar: Libro auxiliar contable general. Registro detallado de movimientos por cuenta con fechas, terceros, débitos, créditos, saldo corrido.
- flujo_de_caja: Estado de flujos de efectivo (NIC 7 / Sección 7 NIIF Pymes). Actividades de operación, inversión y financiación.
- cambios_patrimonio: Estado de cambios en el patrimonio (NIC 1 / Sección 6 NIIF Pymes). Movimientos de capital, reservas, resultados acumulados.
- notas_estados_financieros: Notas explicativas a los estados financieros. Revelaciones de políticas contables, estimaciones, contingencias, impuestos diferidos.
- libro_diario: Libro diario oficial. Registro cronológico de todos los comprobantes contables con cuentas PUC, terceros y valores.

- otro: Documento que no encaja en ninguna de las categorías anteriores.

Contenido del documento:
---
{text_preview}
---

Clasifica el documento aplicando las reglas en ESTE ORDEN. La primera que coincida gana:

REGLAS PRIORIDAD 1 — TÍTULO O ENCABEZADO EXPLÍCITO (buscar en las primeras líneas)
- Título contiene "BALANCE GENERAL" o "ESTADO DE SITUACIÓN FINANCIERA" → balance_general
- Título contiene "ESTADO DE RESULTADOS", "ESTADO DE PÉRDIDAS Y GANANCIAS", "PyG", "P y G" o "P&G" → estado_resultados
- Título contiene "ANEXO IVA", "ANEXO DE IVA", "ANEXO DECLARACIÓN DE IVA" o "ANEXO DECLARACION IVA" → anexo_iva. CRÍTICO: tiene prioridad ABSOLUTA sobre estado_resultados aunque el documento contenga cifras numéricas o tablas de valores.
- Título contiene "ANEXO TRIBUTARIO" o "ANEXO DECLARACIÓN" (de otro impuesto distinto a IVA) → anexo_tributario
- Título contiene "AUXILIAR IVA", "AUXILIAR DE IVA" o "AUXILIAR DE CUENTAS DE IVA" → auxiliar_iva
- Título contiene "LIBRO AUXILIAR", "LIBRO MAYOR", "AUXILIAR POR CUENTA" o "MAYOR Y BALANCES" (sin calificador de impuesto) → libro_auxiliar. NOTA: si el documento lista movimientos con prefijos CE, RC o FV, esos son referencias a comprobantes DENTRO del libro — no cambian el tipo del documento.
- Título contiene "LIBRO DIARIO" → libro_diario
- Título contiene "FLUJO DE CAJA", "FLUJO DE EFECTIVO" o "ESTADO DE FLUJOS DE EFECTIVO" → flujo_de_caja
- Título contiene "ESTADO DE CAMBIOS EN EL PATRIMONIO" → cambios_patrimonio
- Título contiene "NOTAS A LOS ESTADOS FINANCIEROS" o "REVELACIONES" → notas_estados_financieros
- Título contiene "CONCILIACIÓN BANCARIA" → conciliacion_bancaria
- Título contiene "EXTRACTO BANCARIO" o "ESTADO DE CUENTA" bancario → extracto_bancario

REGLAS PRIORIDAD 2 — PREFIJOS Y SEÑALES ESTRUCTURALES
- Tiene CUFE o resolución DIAN → factura_venta (si es emitida) o factura_compra (si es recibida)
- Prefijo "DS"/"DM" en el número o proveedor "No responsable de IVA" o "Generado por: Solución Gratuita DIAN" → documento_soporte
- Prefijo "CE" o texto "Comprobante de Egreso" → comprobante_egreso
- Prefijo "RC" o texto "Recibo de Caja" → recibo_caja
- Movimientos de cuentas 2408xx (IVA) → auxiliar_iva
- Cuentas de impuestos específicos (retención, ICA) en libro auxiliar → auxiliar_impuesto
- Formulario 300 DIAN (IVA bimestral) → declaracion_iva

REGLAS PRIORIDAD 3 — HUELLAS DE CONTENIDO (aplica cuando no hay título explícito)

Busca los marcadores específicos en el CUERPO del documento. La primera coincidencia gana.

Impuestos y declaraciones:
- Contiene "IVA generado" Y "IVA descontable" Y ("base gravable por tarifa" O "tarifa 19%" O "tarifa 5%") → anexo_iva. CRÍTICO: tiene prioridad absoluta sobre estado_resultados aunque haya cifras numéricas.
- Contiene "Formulario 300" O ("renglón" Y "DIAN" Y ("IVA" O "impuesto sobre las ventas")) → declaracion_iva
- Contiene ("ICA" O "industria y comercio") Y "CIIU" Y ("tarifa por mil" O "ingresos brutos gravables") → declaracion_ica
- Contiene ("autorretención" O "autorretencion") Y "ICA" Y "base gravable" → autorretencion_ica
- Contiene "IVA descontable" Y "cuentas 2408" Y ("débito" O "crédito" O "saldo") → auxiliar_iva
- Contiene ("retención en la fuente" O "retefuente") Y "tercero" Y "NIT" Y "base" Y "tarifa" → anexo_tributario

Estados financieros:
- Contiene "activos corrientes" Y "pasivos corrientes" Y "patrimonio" Y ("ecuación contable" O "total activos") → balance_general
- Contiene ("utilidad operacional" O "utilidad neta") Y ("ingresos operacionales" O "ingresos ordinarios") Y ("gastos operacionales" O "gastos de administración") → estado_resultados. NOTA: solo aplica si NO hay marcadores de IVA generado/descontable.
- Contiene "saldo inicial" Y "débito" Y "crédito" Y "saldo final" Y cuenta PUC (4 o más dígitos) → libro_auxiliar
- Contiene ("flujo neto de operación" O "actividades de operación") Y ("actividades de inversión" O "actividades de financiación") → flujo_de_caja
- Contiene "estado de cambios" Y ("capital social" O "reservas" O "resultados acumulados") Y "saldo inicial" → cambios_patrimonio
- Contiene ("nota 1" O "nota 2") Y ("políticas contables" O "estimaciones" O "contingencias") → notas_estados_financieros
- Contiene "partida" Y "comprobante" Y "cuenta" Y "tercero" Y ("débito" Y "crédito") con fechas cronológicas → libro_diario

Documentos fuente y comprobantes:
- Contiene "devengado" Y "deducciones" Y ("neto a pagar" O "salario básico") Y empleados → nomina
- Contiene ("PILA" O "planilla") Y "ARL" Y "caja de compensación" Y ("salud" Y "pensión") → planilla_seguridad_social
- Contiene "saldo anterior" Y "saldo final" Y movimientos con fecha/débito/crédito bancarios → extracto_bancario
- Contiene "concilia" Y ("saldo en libros" O "saldo según libros") Y ("saldo en extracto" O "saldo según extracto") → conciliacion_bancaria
- Contiene "beneficiario" Y ("retenciones aplicadas" O "valor neto") Y ("forma de pago" O "cheque" O "transferencia") SIN CUFE → comprobante_egreso
- Contiene "recibido de" Y "concepto" Y "valor" Y ("forma de pago" O "efectivo") Y "recibo No" → recibo_caja
- Contiene "prestador" Y "contratante" Y "concepto del servicio" Y "valor" SIN resolución DIAN → cuenta_cobro
- Contiene empleados Y ("aporte salud" O "aporte pensión") Y ("salario base de cotización") → planilla_seguridad_social
- Contiene "tipo de impuesto" Y ("valor principal" O "valor pagado") Y ("referencia de pago" O "banco") → recibo_pago_impuesto

Clasifica como "otro" SOLO si ninguna regla de prioridad 1, 2 o 3 coincide.

Extrae también el NIT de la entidad, el nombre, y el período si están presentes."""


__all__ = [
    "RawTransaction",
    "RawTransactionsList",
    "AsientoContable",
    "ContadorOutput",
    "AuditorHallazgo",
    "AuditorOutput",
    "TaxJustification",
    "TaxRateLookup",
    "ExplicacionResultado",
    "PrediccionPeriodo",
    "InterpretacionRatio",
    "ReporteroAnalysis",
    "ReporteroBriefAnalysis",
    "ChatIntentClassification",
    "ChatbotResponse",
    "GENERAL_EXTRACTION_INSTRUCTIONS",
    "ClassificationResponse",
    "CLASSIFICATION_PROMPT",
]
