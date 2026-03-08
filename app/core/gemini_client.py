import logging
from functools import lru_cache
from typing import Optional, List, Dict, Any

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RawTransaction(BaseModel):
    """Structured schema for extracted receipt/invoice data."""
    fecha: Optional[str] = Field(None, description="Date in YYYY-MM-DD format")
    nit_emisor: str = Field(description="NIT of the issuer")
    nit_receptor: str = Field(description="NIT of the receiver (empresa)")
    total: float = Field(description="Total amount of the transaction")
    descripcion: Optional[str] = Field(None, description="Description/concept of the transaction")
    items: Optional[List[Dict[str, Any]]] = Field(None, description="Line items")

class RawTransactionsList(BaseModel):
    transactions: List[RawTransaction] = Field(default_factory=list, description="Extracted list of transactions from the document")


class AsientoContableGemini(BaseModel):
    """Simplified journal entry schema for Gemini structured output."""
    cuenta_puc: str = Field(description="PUC account code (1-6 digits)")
    descripcion: str = Field(description="Description of the entry")
    tipo_movimiento: str = Field(description="DEBITO or CREDITO")
    valor: float = Field(description="Amount of the entry")


class ContadorOutputGemini(BaseModel):
    """ContadorOutput-compatible schema for Gemini structured output."""
    fecha_registro: str = Field(description="Accounting registration date YYYY-MM-DD")
    tipo_documento: str = Field(description="Document type: recibo, factura, extracto, nota_credito, nota_debito, comprobante_egreso, otro")
    descripcion_general: str = Field(description="General description of the accounting event")
    asientos: List[AsientoContableGemini] = Field(description="Journal entries (at least one debit and one credit)")
    total_debitos: float = Field(description="Sum of all debit entries")
    total_creditos: float = Field(description="Sum of all credit entries")


class TaxJustification(BaseModel):
    """Structured output for Gemini tax justification calls."""
    referencias: List[str] = Field(description="Legal articles cited, e.g. ['Art. 383 ET', 'Decreto 2048/1992']")
    justificacion: str = Field(description="Spanish explanation of why these rates apply to the transaction")
    confirma_tasas: bool = Field(description="True if the normative context confirms the calculated rates")


class GeminiClient:
    """Wrapper for Google Generative AI (Gemini) API using LangChain with structured output."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """
        Initialize Gemini client via LangChain with structured output.
        """
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model or settings.gemini_model

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set and not provided")

        # Create base model with structured output capability
        self.model = ChatGoogleGenerativeAI(
            model=self.model_name,
            google_api_key=self.api_key,
            temperature=0.0,
            max_output_tokens=2048,
        )

        # Bind structured output schemas using Pydantic models
        self.structured_model = self.model.with_structured_output(RawTransactionsList)
        self.contador_model = self.model.with_structured_output(ContadorOutputGemini)
        self.tax_model = self.model.with_structured_output(TaxJustification)

    def extract_transactions(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """
        Extract structured data into RawTransaction items using Gemini.
        """
        prompt = f"""Eres un contable experto en lectura de recibos, facturas y comprobantes colombianos.

Texto extraído del documento:
---
{text}
---

Extrae la información como una lista de transacciones. 
Asegúrate de obtener el NIT emisor, NIT receptor, total, concepto/descripción y fecha."""

        if correction_feedback:
            prompt += f"""

=== CORRECCIÓN REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y vuelve a extraer la información."""

        try:
            # Use structured output model - guarantees valid schema
            message = HumanMessage(content=prompt)
            response = self.structured_model.invoke([message])

            # Response is a RawTransactionsList, convert to dict
            data = response.model_dump()
            logger.debug("Extracted receipt data: %s", data)
            return data

        except ValueError as e:
            logger.error(f"Validation error in structured output: {str(e)}")
            raise ValueError(f"Invalid extracted data format: {str(e)}")
        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            raise

    def extract_contador_output(
        self,
        raw_transactions: list,
        *,
        correction_feedback: str | None = None,
    ) -> dict:
        """
        Call Gemini to produce ContadorOutput-compatible JSON from raw transactions.

        Uses structured output (contador_model) to guarantee schema compliance.
        Returns a dict matching ContadorOutputGemini fields.
        """
        txns_text = "\n".join(
            f"- Fecha: {t.get('fecha', 'N/A')}, NIT emisor: {t.get('nit_emisor', 'N/A')}, "
            f"Total: {t.get('total', 0)}, Descripción: {t.get('descripcion', 'N/A')}"
            for t in raw_transactions
        )

        prompt = f"""Eres un contador experto en normativa colombiana (PUC).

Transacciones pendientes de clasificar:
{txns_text}

Genera el asiento contable siguiendo el Plan Único de Cuentas (PUC) colombiano.
- Usa cuentas PUC reales (ej: 5195 para gastos, 1110 para bancos/caja)
- Garantiza que el total de débitos == total de créditos (partida doble)
- tipo_movimiento debe ser "debito" o "credito" (minúsculas)
- tipo_documento debe ser uno de: recibo, factura, extracto, nota_credito, nota_debito, comprobante_egreso, otro"""

        if correction_feedback:
            prompt += f"""

=== CORRECCIÓN REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y regenera el asiento contable."""

        try:
            message = HumanMessage(content=prompt)
            response = self.contador_model.invoke([message])
            data = response.model_dump()
            logger.debug("Contador output generated: %s", data)
            return data
        except ValueError as e:
            logger.error(f"Validation error in contador structured output: {str(e)}")
            raise ValueError(f"Invalid contador output format: {str(e)}")
        except Exception as e:
            logger.error(f"Gemini API error in extract_contador_output: {str(e)}")
            raise

    def justify_tax_analysis(
        self,
        tax_amounts: dict,
        rag_context: str,
    ) -> TaxJustification:
        """
        Call Gemini with calculated tax amounts and RAG normative context.

        Returns a validated TaxJustification object with legal references,
        a Spanish justification, and a confirmation of the applied rates.
        Falls back to a static response if the Gemini call fails.
        """
        retefuente = tax_amounts.get("retefuente", 0)
        reteica = tax_amounts.get("reteica", 0)
        iva = tax_amounts.get("iva", 0)
        tasa_retefuente = tax_amounts.get("tasa_retefuente", "11%")
        tasa_reteica = tax_amounts.get("tasa_reteica", "0.69%")
        tasa_iva = tax_amounts.get("tasa_iva", "19%")
        tipo_transaccion = tax_amounts.get("tipo_transaccion", "servicios")

        normativa_section = rag_context.strip() if rag_context else "No se encontró normativa en la base vectorial."

        prompt = f"""Eres un experto tributario colombiano.

Esta transacción de tipo "{tipo_transaccion}" requiere las siguientes retenciones:
- Retefuente: ${retefuente:,.0f} (tasa {tasa_retefuente})
- ReteICA: ${reteica:,.0f} (tasa {tasa_reteica})
- IVA: ${iva:,.0f} (tasa {tasa_iva})

Normativa aplicable recuperada de la base vectorial:
---
{normativa_section}
---

Con base en la normativa anterior:
1. Confirma si las tasas aplicadas son correctas para este tipo de transacción.
2. Cita los artículos específicos del Estatuto Tributario o decretos que fundamentan cada retención.
3. Proporciona una justificación breve en español.

Devuelve tu análisis con las referencias legales, la justificación y si confirmas las tasas."""

        try:
            message = HumanMessage(content=prompt)
            response = self.tax_model.invoke([message])
            logger.debug("Tax justification generated: %s", response)
            return response
        except Exception as e:
            logger.warning(
                f"Gemini tax justification failed (using fallback): {str(e)}"
            )
            return TaxJustification(
                referencias=["Art. 383 ET", "Art. 401 ET", "Art. 477 ET", "Decreto 2048/1992"],
                justificacion=(
                    "Retenciones aplicadas según tasas vigentes del Estatuto Tributario colombiano. "
                    "Retefuente según Art. 383 ET para servicios; ReteICA según tarifas municipales; "
                    "IVA según Art. 477 ET tarifa general."
                ),
                confirma_tasas=True,
            )


@lru_cache(maxsize=1)
def get_gemini_client() -> GeminiClient:
    """Return the singleton GeminiClient instance (cached after first call)."""
    return GeminiClient()
