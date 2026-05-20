"""
Document type taxonomy and ingestion pathway classification.

Defines the types of documents the system can ingest and maps them
to one of two pathways:
  - BUILD_FROM_SCRATCH (Vía A): source documents → build accounting from zero
  - WORK_WITH_EXISTING (Vía B): existing financial statements → use for reports
"""

from enum import Enum


class ParserMode(str, Enum):
    """LlamaParse extraction quality modes."""

    FAST = "fast"
    STANDARD = "standard"
    PREMIUM = "premium"
    GPT4O = "gpt4o"


class DocumentType(str, Enum):
    """Types of documents the system can ingest."""

    # Vía A — Source documents (build accounting from scratch)
    FACTURA_VENTA = "factura_venta"
    FACTURA_COMPRA = "factura_compra"
    EXTRACTO_BANCARIO = "extracto_bancario"
    NOTA_CREDITO = "nota_credito"
    NOTA_DEBITO = "nota_debito"
    DECLARACION_IVA = "declaracion_iva"
    DECLARACION_RETEICA = "declaracion_reteica"
    ANEXO_TRIBUTARIO = "anexo_tributario"
    AUXILIAR_IMPUESTO = "auxiliar_impuesto"
    DECLARACION_ICA = "declaracion_ica"
    AUTORRETENCION_ICA = "autorretencion_ica"
    ANEXO_IVA = "anexo_iva"
    AUXILIAR_IVA = "auxiliar_iva"
    COMPROBANTE_EGRESO = "comprobante_egreso"
    DOCUMENTO_SOPORTE = "documento_soporte"
    RECIBO_CAJA = "recibo_caja"
    NOMINA = "nomina"
    LIQUIDACION_CESANTIAS = "liquidacion_cesantias"
    CONCILIACION_BANCARIA = "conciliacion_bancaria"
    CUENTA_COBRO = "cuenta_cobro"
    PLANILLA_SEGURIDAD_SOCIAL = "planilla_seguridad_social"
    RECIBO_PAGO_IMPUESTO = "recibo_pago_impuesto"

    # Vía B — Existing financial statements (use for derived reports)
    BALANCE_GENERAL = "balance_general"
    BALANCE_GENERAL_ANTERIOR = "balance_general_anterior"
    ESTADO_RESULTADOS = "estado_resultados"
    LIBRO_AUXILIAR = "libro_auxiliar"
    FLUJO_DE_CAJA = "flujo_de_caja"
    CAMBIOS_PATRIMONIO = "cambios_patrimonio"
    NOTAS_ESTADOS_FINANCIEROS = "notas_estados_financieros"
    LIBRO_DIARIO = "libro_diario"

    # Fallback
    OTRO = "otro"


class IngestPathway(str, Enum):
    """Two ingestion pathways defined by stakeholder."""

    BUILD_FROM_SCRATCH = "build_from_scratch"
    WORK_WITH_EXISTING = "work_with_existing"


# Maps each document type to its pathway
PATHWAY_MAP: dict[DocumentType, IngestPathway] = {
    # Vía A — source documents
    DocumentType.FACTURA_VENTA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.FACTURA_COMPRA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.EXTRACTO_BANCARIO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.NOTA_CREDITO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.NOTA_DEBITO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.DECLARACION_IVA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.DECLARACION_RETEICA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.ANEXO_TRIBUTARIO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.AUXILIAR_IMPUESTO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.DECLARACION_ICA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.AUTORRETENCION_ICA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.ANEXO_IVA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.AUXILIAR_IVA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.COMPROBANTE_EGRESO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.DOCUMENTO_SOPORTE: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.RECIBO_CAJA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.NOMINA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.LIQUIDACION_CESANTIAS: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.CONCILIACION_BANCARIA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.CUENTA_COBRO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.PLANILLA_SEGURIDAD_SOCIAL: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.RECIBO_PAGO_IMPUESTO: IngestPathway.BUILD_FROM_SCRATCH,
    # Vía B — existing financial statements
    DocumentType.BALANCE_GENERAL: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.BALANCE_GENERAL_ANTERIOR: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.ESTADO_RESULTADOS: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.LIBRO_AUXILIAR: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.FLUJO_DE_CAJA: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.CAMBIOS_PATRIMONIO: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.NOTAS_ESTADOS_FINANCIEROS: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.LIBRO_DIARIO: IngestPathway.WORK_WITH_EXISTING,
    # Fallback
    DocumentType.OTRO: IngestPathway.BUILD_FROM_SCRATCH,
}


def get_pathway(doc_type: DocumentType) -> IngestPathway:
    """Return the ingestion pathway for a document type."""
    return PATHWAY_MAP.get(doc_type, IngestPathway.BUILD_FROM_SCRATCH)


DOCUMENT_TYPE_LABELS: dict[DocumentType, str] = {
    DocumentType.FACTURA_VENTA: "Factura de venta",
    DocumentType.FACTURA_COMPRA: "Factura de compra",
    DocumentType.EXTRACTO_BANCARIO: "Extracto bancario",
    DocumentType.NOTA_CREDITO: "Nota credito",
    DocumentType.NOTA_DEBITO: "Nota debito",
    DocumentType.DECLARACION_IVA: "Declaracion IVA",
    DocumentType.DECLARACION_RETEICA: "Declaracion ReteICA",
    DocumentType.ANEXO_TRIBUTARIO: "Anexo tributario",
    DocumentType.AUXILIAR_IMPUESTO: "Auxiliar de impuesto",
    DocumentType.DECLARACION_ICA: "Declaracion ICA",
    DocumentType.AUTORRETENCION_ICA: "Autorretencion ICA",
    DocumentType.ANEXO_IVA: "Anexo IVA",
    DocumentType.AUXILIAR_IVA: "Auxiliar IVA",
    DocumentType.COMPROBANTE_EGRESO: "Comprobante de egreso",
    DocumentType.DOCUMENTO_SOPORTE: "Documento soporte",
    DocumentType.RECIBO_CAJA: "Recibo de caja",
    DocumentType.NOMINA: "Nomina",
    DocumentType.LIQUIDACION_CESANTIAS: "Liquidacion de cesantias",
    DocumentType.CONCILIACION_BANCARIA: "Conciliacion bancaria",
    DocumentType.CUENTA_COBRO: "Cuenta de cobro",
    DocumentType.PLANILLA_SEGURIDAD_SOCIAL: "Planilla seguridad social",
    DocumentType.RECIBO_PAGO_IMPUESTO: "Recibo de pago de impuesto",
    DocumentType.BALANCE_GENERAL: "Balance general",
    DocumentType.BALANCE_GENERAL_ANTERIOR: "Balance general anterior",
    DocumentType.ESTADO_RESULTADOS: "Estado de resultados",
    DocumentType.LIBRO_AUXILIAR: "Libro auxiliar",
    DocumentType.FLUJO_DE_CAJA: "Flujo de caja",
    DocumentType.CAMBIOS_PATRIMONIO: "Cambios en el patrimonio",
    DocumentType.NOTAS_ESTADOS_FINANCIEROS: "Notas a estados financieros",
    DocumentType.LIBRO_DIARIO: "Libro diario",
    DocumentType.OTRO: "Otro",
}


def get_document_type_label(doc_type: DocumentType) -> str:
    """Return the Spanish display label for a document type."""
    return DOCUMENT_TYPE_LABELS.get(doc_type, doc_type.value)


def list_document_type_options() -> list[dict[str, str]]:
    """Return all document types as value/label pairs."""
    return [
        {"value": doc_type.value, "label": get_document_type_label(doc_type)}
        for doc_type in DocumentType
    ]


_VIA_B_TYPES: frozenset[DocumentType] = frozenset(
    {
        DocumentType.BALANCE_GENERAL,
        DocumentType.BALANCE_GENERAL_ANTERIOR,
        DocumentType.ESTADO_RESULTADOS,
        DocumentType.LIBRO_AUXILIAR,
    }
)


def list_via_a_document_type_options() -> list[dict[str, str]]:
    """Return document types valid for Vía A uploads (excludes Vía B financial statements)."""
    return [
        {"value": doc_type.value, "label": get_document_type_label(doc_type)}
        for doc_type in DocumentType
        if doc_type not in _VIA_B_TYPES
    ]
