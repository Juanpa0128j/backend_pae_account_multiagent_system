"""
Gemini API client wrapper.
Handles communication with Google's Gemini 2.5 Flash model via LangChain.
Uses structured output to ensure JSON schema compliance.
"""

import os
import logging
from typing import Optional, Literal
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
        "recibo", "factura", "extracto", "nota_credito", 
        "nota_debito", "comprobante_egreso", "otro"
    ] = Field(description="Document type")


class GeminiClient:
    """Wrapper for Google Generative AI (Gemini) API using LangChain with structured output."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        """
        Initialize Gemini client via LangChain with structured output.
        
        Args:
            api_key: Google AI API key. If None, reads from GOOGLE_API_KEY env var.
            model: Model name to use (default: gemini-2.5-flash for free tier)
        """
        # Try multiple env var names for broad compatibility
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        self.model_name = model
        
        if not self.api_key:
            raise ValueError(
                "Gemini API key not set. Provide GEMINI_API_KEY or GOOGLE_API_KEY env var."
            )
        
        # Create base model with structured output capability
        self.model = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=self.api_key,
            temperature=0.0,
            max_output_tokens=512
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
            logger.info(f"Extracted receipt data: {data}")
            return data
            
        except ValueError as e:
            logger.error(f"Validation error in structured output: {str(e)}")
            raise ValueError(f"Invalid extracted data format: {str(e)}")
        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            raise


def get_gemini_client() -> GeminiClient:
    """Factory function to get/create Gemini client."""
    return GeminiClient()
