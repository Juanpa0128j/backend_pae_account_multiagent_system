"""
Gemini API client wrapper.
Handles communication with Google's Gemini 2.5 Flash model via LangChain.
Uses structured output to ensure JSON schema compliance.
"""

import logging
from functools import lru_cache
from typing import Literal, Optional

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ReceiptData(BaseModel):
    """Structured schema for extracted receipt/invoice data."""

    fecha: Optional[str] = Field(None, description="Date in YYYY-MM-DD format")
    monto: float = Field(description="Amount/total")
    concepto: str = Field(description="Concept/description of payment")
    beneficiario: str = Field(description="Recipient/beneficiary")
    empresa: str = Field(description="Issuing company/bank")
    referencia: Optional[str] = Field(None, description="Transaction reference number")
    tipo_documento: Literal[
        "recibo",
        "factura",
        "extracto",
        "nota_credito",
        "nota_debito",
        "comprobante_egreso",
        "otro",
    ] = Field(description="Document type")


class GeminiClient:
    """Wrapper for Google Generative AI (Gemini) API using LangChain with structured output."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """
        Initialize Gemini client via LangChain with structured output.

        Args:
            api_key: Google AI API key. If None, reads from settings.
            model: Model name to use. If None, reads from settings.
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
            max_output_tokens=512,
        )

        # Bind structured output schema using Pydantic model
        self.structured_model = self.model.with_structured_output(ReceiptData)

    def extract_receipt_data(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """
        Extract structured data from receipt/invoice text using Gemini.
        Uses structured output to guarantee JSON schema compliance.

        Args:
            text: Raw text extracted from PDF
            correction_feedback: If provided, appended to the prompt so the
                model can correct its previous invalid output.

        Returns:
            Dictionary with extracted receipt data
        """
        prompt = f"""Eres un experto en lectura de recibos y facturas.

Texto extraído del documento:
---
{text}
---

Extrae la siguiente información del documento."""

        if correction_feedback:
            prompt += f"""

=== CORRECCIÓN REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y vuelve a extraer la información."""

        try:
            # Use structured output model - guarantees valid ReceiptData schema
            message = HumanMessage(content=prompt)
            response = self.structured_model.invoke([message])

            # Response is already a ReceiptData object, convert to dict
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
