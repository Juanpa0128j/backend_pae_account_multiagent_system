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

    # Vía B — Existing financial statements (use for derived reports)
    BALANCE_GENERAL = "balance_general"
    ESTADO_RESULTADOS = "estado_resultados"
    LIBRO_AUXILIAR = "libro_auxiliar"

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
    # Vía B — existing financial statements
    DocumentType.BALANCE_GENERAL: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.ESTADO_RESULTADOS: IngestPathway.WORK_WITH_EXISTING,
    DocumentType.LIBRO_AUXILIAR: IngestPathway.WORK_WITH_EXISTING,
    # Fallback
    DocumentType.OTRO: IngestPathway.BUILD_FROM_SCRATCH,
}


def get_pathway(doc_type: DocumentType) -> IngestPathway:
    """Return the ingestion pathway for a document type."""
    return PATHWAY_MAP.get(doc_type, IngestPathway.BUILD_FROM_SCRATCH)
