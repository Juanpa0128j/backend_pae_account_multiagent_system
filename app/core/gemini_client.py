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

        # Bind structured output schema using Pydantic model
        self.structured_model = self.model.with_structured_output(RawTransactionsList)

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

@lru_cache(maxsize=1)
def get_gemini_client() -> GeminiClient:
    """Return the singleton GeminiClient instance (cached after first call)."""
    return GeminiClient()
