"""Reportero prompt builders.

Generates prompts for financial report brief analysis and comprehensive analysis.
"""

from __future__ import annotations

import json

__all__ = [
    "reportero_brief",
    "reportero_analysis",
]


def reportero_brief(
    report_type: str,
    report_data: dict,
    rag_context: str,
) -> str:
    """Return the brief report analysis prompt string."""
    return f"""Eres un Director Financiero experto en contabilidad colombiana (NIIF, PUC, Estatuto Tributario).

Analiza el siguiente reporte de tipo '{report_type}' y proporciona:
1. Un resumen ejecutivo breve (1-2 párrafos)
2. Los 3-5 puntos clave más importantes
3. Alertas de riesgo si las hay
4. 1-3 recomendaciones accionables

=== DATOS DEL REPORTE ===
{json.dumps(report_data, ensure_ascii=False, indent=2, default=str)}

=== CONTEXTO NORMATIVO ===
{rag_context if rag_context else "Sin contexto normativo adicional."}

Responde en español."""


def reportero_analysis(
    financial_data: dict,
    rag_context: str,
    system_prompt: str,
) -> str:
    """Return the comprehensive financial analysis prompt string."""
    return f"""{system_prompt}

=== DATOS FINANCIEROS A ANALIZAR ===
{json.dumps(financial_data, ensure_ascii=False, indent=2, default=str)}

=== CONTEXTO NORMATIVO (RAG) ===
{rag_context if rag_context else "Sin contexto normativo adicional disponible."}

Genera el análisis financiero completo siguiendo la estructura requerida.
Todas las respuestas deben ser en español."""
