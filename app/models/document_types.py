"""
Document type taxonomy and ingestion pathway classification.

Defines the types of documents the system can ingest and maps them
to one of two pathways:
  - BUILD_FROM_SCRATCH (Vía A): source documents → build accounting from zero
  - WORK_WITH_EXISTING (Vía B): existing financial statements → use for reports
"""

from enum import Enum


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
    CONCILIACION_BANCARIA = "conciliacion_bancaria"
    CUENTA_COBRO = "cuenta_cobro"
    PLANILLA_SEGURIDAD_SOCIAL = "planilla_seguridad_social"
    RECIBO_PAGO_IMPUESTO = "recibo_pago_impuesto"

    # Vía B — Existing financial statements (use for derived reports)
    BALANCE_GENERAL = "balance_general"
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
    DocumentType.CONCILIACION_BANCARIA: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.CUENTA_COBRO: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.PLANILLA_SEGURIDAD_SOCIAL: IngestPathway.BUILD_FROM_SCRATCH,
    DocumentType.RECIBO_PAGO_IMPUESTO: IngestPathway.BUILD_FROM_SCRATCH,
    # Vía B — existing financial statements
    DocumentType.BALANCE_GENERAL: IngestPathway.WORK_WITH_EXISTING,
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
