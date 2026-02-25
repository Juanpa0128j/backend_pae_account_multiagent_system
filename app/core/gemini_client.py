"""
Gemini API client wrapper.
Handles communication with Google's Gemini 2.5 Flash model.
"""

import os
import json
import logging
from typing import Optional
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class GeminiClient:
    """Wrapper for Google Generative AI (Gemini) API."""
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        """
        Initialize Gemini client.
        
        Args:
            api_key: Google AI API key. If None, reads from GEMINI_API_KEY env var.
            model: Model name to use (default: gemini-2.5-flash for free tier)
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model
        
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set and not provided")
        
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model)
    
    def extract_receipt_data(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """
        Extract structured data from receipt/invoice text using Gemini.
        
        Args:
            text: Raw text extracted from PDF
            correction_feedback: If provided, appended to the prompt so the
                model can correct its previous invalid output.
            
        Returns:
            Dictionary with keys: fecha, monto, concepto, beneficiario, empresa, referencia, tipo_documento
        """
        prompt = f"""Eres un experto en lectura de recibos y facturas.

Texto extraído del documento:
---
{text}
---

Extrae la siguiente información en JSON válido (responde SOLO JSON, sin explicación):
{{
  "fecha": "YYYY-MM-DD o null",
  "monto": 0.00 (número),
  "concepto": "descripción del pago",
  "beneficiario": "quien recibe",
  "empresa": "empresa/banco emisor",
  "referencia": "número de transacción o null",
  "tipo_documento": "recibo|factura|extracto|nota_credito|nota_debito|comprobante_egreso|otro"
}}"""

        if correction_feedback:
            prompt += f"""

=== CORRECCIÓN REQUERIDA ===
{correction_feedback}

Genera nuevamente el JSON corrigiendo los errores indicados. Solo JSON, sin texto adicional.""""""
        
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,  # Deterministic for structured extraction
                    max_output_tokens=512,
                )
            )
            
            # Parse JSON from response
            response_text = response.text.strip()
            
            # Try to extract JSON if wrapped in markdown
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()
            
            data = json.loads(response_text)
            logger.info(f"Extracted data: {data}")
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response as JSON: {response_text}")
            raise ValueError(f"Invalid JSON from Gemini: {str(e)}")
        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            raise


def get_gemini_client() -> GeminiClient:
    """Factory function to get/create Gemini client."""
    return GeminiClient()
