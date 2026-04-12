"""
gemini_client — backward-compatibility shim. DO NOT ADD CODE HERE.

All Pydantic schemas have moved to app.models.llm_schemas.
All extraction logic lives in app.core.llm_client.LLMClient.
All LLM providers live in app.core.providers.*.

This file exists only to avoid breaking external callers during migration.
It will be deleted once all import sites point to the correct modules.
"""

# Re-export schemas from their canonical location
from app.models.llm_schemas import (  # noqa: F401
    AsientoContableGemini,
    AuditorHallazgoGemini,
    AuditorOutputGemini,
    ChatbotResponseGemini,
    ChatIntentClassification,
    ContadorOutputGemini,
    ExplicacionResultadoGemini,
    InterpretacionRatioGemini,
    PrediccionPeriodoGemini,
    RawTransaction,
    RawTransactionsList,
    ReporteroBriefAnalysisGemini,
    ReporteroAnalysisGemini,
    TaxJustification,
    TaxRateLookup,
)

# Re-export ingest schemas
from app.models.ingest_schemas import (  # noqa: F401
    AnexoIVAContent,
    AutoretencionICAContent,
    AuxiliarIVAContent,
    AuxiliaryLedgerContent,
    BalanceGeneralContent,
    BankStatementContent,
    CambiosPatrimonioContent,
    ConciliacionBancariaContent,
    ComprobanteEgresoContent,
    CuentaCobroContent,
    DeclaracionICAContent,
    DocumentoSoporteContent,
    EstadoResultadosContent,
    FacturaCompraContent,
    FacturaVentaContent,
    FinancialStatementContent,
    FlujoDeCajaContent,
    LibroDiarioContent,
    NominaContent,
    NotaCreditoContent,
    NotaDebitoContent,
    NotasEstadosFinancierosContent,
    PlanillaSegSocialContent,
    ReciboCajaContent,
    ReciboPagoImpuestoContent,
    TaxDeclarationContent,
)

# Extraction instructions constant (used by some tests/scripts)
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

# Backward-compatibility aliases — use app.core.llm_client directly instead
from app.core.llm_client import (  # noqa: F401, E402
    LLMClient as GeminiClient,
    get_llm_client,
)


def get_gemini_client() -> GeminiClient:
    """Deprecated. Use get_llm_client() from app.core.llm_client instead."""
    return get_llm_client()
