"""
Document classifier service.

Classifies uploaded documents by type and ingestion pathway using LLM
analysis of document content. Automatically selects the best available
LLM provider: OpenAI → Gemini → Groq (first available key wins).
"""

import copy
import logging
from functools import lru_cache
from typing import Literal, Optional, cast

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field, SecretStr

from app.core.config import get_settings
from app.models.document_types import DocumentType, IngestPathway, get_pathway

logger = logging.getLogger(__name__)

SourceFormat = Literal["pdf", "xlsx", "xml", "jpg", "jpeg", "png"]


class DocumentClassification(BaseModel):
    """Result of classifying an uploaded document."""

    doc_type: DocumentType = Field(description="Type of document detected")
    pathway: IngestPathway = Field(description="Ingestion pathway for this document")
    confidence: float = Field(ge=0, le=1, description="Classification confidence 0-1")
    source_format: SourceFormat = Field(description="Source file format")
    period_start: Optional[str] = Field(
        default=None, description="Period start date YYYY-MM-DD if detected"
    )
    period_end: Optional[str] = Field(
        default=None, description="Period end date YYYY-MM-DD if detected"
    )
    entity_nit: Optional[str] = Field(
        default=None, description="Entity NIT if detected in document"
    )
    entity_name: Optional[str] = Field(
        default=None, description="Entity name if detected in document"
    )


# The classification prompt with full taxonomy
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

Clasifica el documento. Reglas clave:
- Si tiene CUFE o resolución DIAN → factura_venta o factura_compra (electrónica)
- Si tiene prefijo "DS" en el número o el proveedor es persona natural sin NIT → documento_soporte
- Si tiene prefijo "CE" o "Comprobante de Egreso" → comprobante_egreso
- Si tiene prefijo "RC" o "Recibo de Caja" → recibo_caja
- Si contiene cuentas PUC con saldos organizados jerárquicamente → estado financiero existente (balance_general, estado_resultados, etc.)
- Si contiene movimientos de IVA (cuentas 2408xx) → auxiliar_iva

Extrae también el NIT de la entidad, el nombre, y el período si están presentes."""


class _ClassificationResponse(BaseModel):
    """Schema for LLM structured output during classification."""

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


def _clean_schema_patterns(schema: dict) -> dict:
    """Recursively remove unsupported lookahead regex patterns for Groq."""
    cleaned = copy.deepcopy(schema)

    def _walk(obj):
        if isinstance(obj, dict):
            if (
                "pattern" in obj
                and isinstance(obj["pattern"], str)
                and "(?!" in obj["pattern"]
            ):
                del obj["pattern"]
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(cleaned)
    return cleaned


@lru_cache(maxsize=1)
def _get_classifier_chain():
    """Build the classification LLM chain using the first available provider.

    Priority: OpenAI → Gemini → Groq.  Raises ValueError if none is configured.
    """
    settings = get_settings()
    cleaned_schema = _clean_schema_patterns(_ClassificationResponse.model_json_schema())
    model = None
    provider = None

    # 1. Try OpenAI
    if settings.openai_api_key:
        try:
            from langchain_openai import ChatOpenAI

            model = ChatOpenAI(
                model="gpt-4o-mini",
                api_key=SecretStr(settings.openai_api_key),
                temperature=0,
            )
            provider = "openai/gpt-4o-mini"
        except Exception as exc:
            logger.warning("doc_classifier: OpenAI init failed: %s", exc)

    # 2. Try Gemini
    if model is None and settings.gemini_api_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            model = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                google_api_key=settings.gemini_api_key,
                temperature=0,
            )
            provider = "gemini/gemini-2.0-flash"
        except Exception as exc:
            logger.warning("doc_classifier: Gemini init failed: %s", exc)

    # 3. Try Groq
    if model is None and settings.groq_api_key:
        try:
            from langchain_groq import ChatGroq

            model = ChatGroq(
                model="openai/gpt-oss-120b",
                api_key=SecretStr(settings.groq_api_key),
                temperature=0,
            )
            provider = "groq/gpt-oss-120b"
        except Exception as exc:
            logger.warning("doc_classifier: Groq init failed: %s", exc)

    if model is None:
        raise ValueError(
            "No LLM API key available for document classification. "
            "Set at least one of: OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY"
        )

    logger.info("doc_classifier: using provider %s", provider)

    # Bind structured output — OpenAI/Groq support json_schema, Gemini uses direct invoke
    if provider and provider.startswith("gemini"):
        # Gemini doesn't support response_format json_schema; parse JSON from text
        return (
            model
            | JsonOutputParser()
            | (lambda payload: _ClassificationResponse.model_validate(payload))
        ), provider
    else:
        bound = model.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": _ClassificationResponse.__name__,
                    "schema": cleaned_schema,
                    "strict": False,
                },
            }
        )
        return (
            bound
            | JsonOutputParser()
            | (lambda payload: _ClassificationResponse.model_validate(payload))
        ), provider


def classify_document(
    text_preview: str,
    source_format: str,
) -> DocumentClassification:
    """
    Classify a document using the best available LLM provider.

    Args:
        text_preview: First ~3000 chars of extracted text.
        source_format: File extension without dot ("pdf", "xlsx", "xml").

    Returns:
        DocumentClassification with type, pathway, and metadata.
    """
    normalized_source_format = source_format.lower().strip()
    if normalized_source_format not in {"pdf", "xlsx", "xml", "jpg", "jpeg", "png"}:
        logger.warning(
            "doc_classifier: unsupported source_format '%s' — coercing to 'pdf'",
            source_format,
        )
        normalized_source_format = "pdf"
    source_format_literal = cast(SourceFormat, normalized_source_format)

    if not text_preview or not text_preview.strip():
        logger.warning("doc_classifier: empty text preview — defaulting to 'otro'")
        return DocumentClassification(
            doc_type=DocumentType.OTRO,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.0,
            source_format=source_format_literal,
        )

    try:
        chain, provider = _get_classifier_chain()

        # Gemini needs the JSON instruction in the prompt
        prompt_content = CLASSIFICATION_PROMPT.format(text_preview=text_preview)
        if provider and provider.startswith("gemini"):
            prompt_content += "\n\nResponde EXCLUSIVAMENTE con un JSON válido con los campos: doc_type, confidence, period_start, period_end, entity_nit, entity_name."

        response = chain.invoke([HumanMessage(content=prompt_content)])

        # Parse the doc_type string into the enum
        try:
            doc_type = DocumentType(response.doc_type)
        except ValueError:
            logger.warning(
                "doc_classifier: LLM returned unknown doc_type '%s' — using 'otro'",
                response.doc_type,
            )
            doc_type = DocumentType.OTRO

        pathway = get_pathway(doc_type)

        classification = DocumentClassification(
            doc_type=doc_type,
            pathway=pathway,
            confidence=response.confidence,
            source_format=source_format_literal,
            period_start=response.period_start,
            period_end=response.period_end,
            entity_nit=response.entity_nit,
            entity_name=response.entity_name,
        )

        logger.info(
            "doc_classifier: [%s] classified as %s (pathway=%s, confidence=%.2f)",
            provider,
            classification.doc_type.value,
            classification.pathway.value,
            classification.confidence,
        )
        return classification

    except Exception as e:
        logger.error("doc_classifier: LLM classification failed: %s", e)
        return DocumentClassification(
            doc_type=DocumentType.OTRO,
            pathway=IngestPathway.BUILD_FROM_SCRATCH,
            confidence=0.0,
            source_format=source_format_literal,
        )
