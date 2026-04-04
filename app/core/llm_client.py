"""
Primary LLM orchestrator.

Provider priority: OpenAI (primary) → Gemini (fallback) → Groq (last resort).
All extraction methods live here. `gemini_client.py` re-exports this for
backward compatibility.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

_QUOTA_SIGNALS = ("RESOURCE_EXHAUSTED", "429", "quota", "rate_limit", "RateLimitError")


def _is_quota_error(exc: Exception) -> bool:
    err = str(exc)
    return any(s.lower() in err.lower() for s in _QUOTA_SIGNALS)


def _compact_error_message(exc: Exception, max_len: int = 240) -> str:
    """Return a compact single-line error message for fallback traces."""
    msg = " ".join(str(exc).split())
    if len(msg) <= max_len:
        return msg
    return f"{msg[: max_len - 3]}..."


class LLMClient:
    """
    Multi-provider LLM client.

    Uses OpenAI as the primary model. Falls back to Gemini, then Groq,
    on quota exhaustion (HTTP 429 / RESOURCE_EXHAUSTED).
    """

    def __init__(self) -> None:
        from app.core.config import get_settings
        settings = get_settings()

        self._openai_key = settings.openai_api_key
        self._gemini_key = settings.gemini_api_key
        self._groq_key = settings.groq_api_key

        if not any([self._openai_key, self._gemini_key, self._groq_key]):
            raise ValueError(
                "No LLM provider API keys configured. Set at least one of: "
                "OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY"
            )

        self._openai: Any = OpenAIProvider() if self._openai_key else None

        # Lazy-initialised on first fallback
        self._gemini: Any = None
        self._groq: Any = None

    # ------------------------------------------------------------------
    # Internal fallback chain
    # ------------------------------------------------------------------

    def _get_gemini(self):
        if self._gemini is None:
            from app.core.providers.gemini_provider import GeminiProvider
            self._gemini = GeminiProvider()
        return self._gemini

    def _get_groq(self):
        if self._groq is None:
            from app.core.providers.groq_provider import GroqProvider
            self._groq = GroqProvider()
        return self._groq

    def _invoke(self, schema_cls: type[BaseModel], prompt: str) -> BaseModel:
        """Invoke with OpenAI → Gemini → Groq fallback chain."""
        providers: list[tuple[str, Any]] = []
        if self._openai is not None:
            providers.append(("OpenAI", self._openai))
        if self._gemini_key:
            providers.append(("Gemini", self._get_gemini()))
        if self._groq_key:
            providers.append(("Groq", self._get_groq()))

        last_exc: Exception | None = None
        failure_trace: list[str] = []
        for idx, (name, provider) in enumerate(providers):
            try:
                return provider.invoke(schema_cls, prompt)
            except Exception as exc:
                last_exc = exc
                failure_trace.append(
                    f"{name}: {exc.__class__.__name__}: {_compact_error_message(exc)}"
                )
                has_next = idx < (len(providers) - 1)
                if not has_next:
                    break

                # Only fall back on quota errors. Permanent failures (auth, invalid API key,
                # schema violations) must be surfaced immediately per CLAUDE.md: "Fail fast."
                if _is_quota_error(exc):
                    logger.warning(
                        "%s quota exceeded — falling back for %s",
                        name,
                        schema_cls.__name__,
                    )
                else:
                    # Non-quota errors are permanent; fail fast and surface immediately.
                    logger.error(
                        "%s permanent failure (%s) — not attempting fallback for %s",
                        name,
                        exc,
                        schema_cls.__name__,
                    )
                    raise

        trace_summary = " | ".join(failure_trace) if failure_trace else str(last_exc)
        raise RuntimeError(
            f"All configured LLM providers failed for {schema_cls.__name__}. "
            f"Attempts: {trace_summary}"
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _as_dict(response: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(response, BaseModel):
            return response.model_dump(mode="json")
        return dict(response)

    @staticmethod
    def _as_model(model_cls: type[ModelT], response: BaseModel | dict[str, Any]) -> ModelT:
        if isinstance(response, model_cls):
            return response
        if isinstance(response, BaseModel):
            return model_cls.model_validate(response.model_dump(mode="json"))
        return model_cls.model_validate(response)

    # ------------------------------------------------------------------
    # Extraction methods
    # ------------------------------------------------------------------

    def extract_transactions(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Legacy method — kept for backward compatibility. Routes to extract_factura_venta."""
        return self.extract_factura_venta(text, correction_feedback=correction_feedback)

    def extract_factura_venta(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Extract factura de venta (electronic sales invoice, DIAN Res. 000165/2023)."""
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, FacturaVentaContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de esta FACTURA DE VENTA electrónica.

Extrae obligatoriamente: número de factura (consecutivo con prefijo), CUFE, fecha de emisión, datos del emisor (NIT con DV, razón social, régimen, resolución de facturación), datos del receptor (NIT, razón social), forma de pago, ítems con descripción/cantidad/valor unitario/impuestos, totales desglosados (subtotal, IVA, retenciones, total a pagar), y retenciones aplicadas (retefuente, reteIVA, reteICA).

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(FacturaVentaContent, prompt))

    def extract_factura_compra(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Extract factura de compra (purchase invoice)."""
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, FacturaCompraContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de esta FACTURA DE COMPRA.

Extrae obligatoriamente: número de factura, CUFE, fecha, datos del proveedor (NIT con DV, razón social, régimen), datos de la empresa receptora, ítems con detalle de IVA y retenciones, totales desglosados, y si aplica, indica si es documento soporte (adquisición a no obligado a facturar).

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(FacturaCompraContent, prompt))

    def extract_nota_credito(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Extract nota crédito (credit note)."""
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, NotaCreditoContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de esta NOTA CRÉDITO electrónica.

Extrae obligatoriamente: consecutivo, CUDE, fecha de emisión, referencia a la factura original (número y CUFE), concepto de la nota (devolución/descuento/anulación/corrección), emisor, receptor, ítems ajustados con sus impuestos, y totales ajustados.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(NotaCreditoContent, prompt))

    def extract_nota_debito(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Extract nota débito (debit note)."""
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, NotaDebitoContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de esta NOTA DÉBITO electrónica.

Extrae obligatoriamente: consecutivo, CUDE, fecha, referencia a la factura original, concepto (intereses/ajuste precio/penalización), emisor, receptor, ítems adicionados con impuestos, y totales adicionados.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(NotaDebitoContent, prompt))

    def extract_contador_output(
        self,
        raw_transactions: list,
        *,
        rag_context: list[dict] | None = None,
        correction_feedback: str | None = None,
    ) -> dict:
        from app.core.gemini_client import ContadorOutputGemini
        txns_text = "\n".join(
            f"- Fecha: {t.get('fecha', 'N/A')}, NIT emisor: {t.get('nit_emisor', 'N/A')}, "
            f"Total: {t.get('total', 0)}, Descripcion: {t.get('descripcion', 'N/A')}"
            for t in raw_transactions
        )

        rag_context = rag_context or []
        rag_lines: list[str] = []
        for item in rag_context[:5]:
            if isinstance(item, dict):
                rag_lines.append(
                    str(item.get("content") or item.get("text") or item.get("document") or item)
                )
            else:
                rag_lines.append(str(getattr(item, "content", item)))
        rag_section = "\n".join(line for line in rag_lines if line).strip()
        if not rag_section:
            rag_section = "Sin contexto normativo adicional."

        prompt = f"""Eres un contador experto en normativa colombiana (PUC).

Transacciones pendientes de clasificar:
{txns_text}

Genera el asiento contable siguiendo el PUC colombiano.
- Usa cuentas PUC reales
- Garantiza que total_debitos == total_creditos
- tipo_movimiento debe ser 'debito' o 'credito'
- tipo_documento debe estar en: recibo, factura, extracto, nota_credito, nota_debito, comprobante_egreso, otro

Contexto normativo/RAG:
{rag_section}"""

        if correction_feedback:
            prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y regenera el asiento contable."""

        try:
            response = self._invoke(ContadorOutputGemini, prompt)
            data = self._as_dict(response)
            logger.debug("Contador output generated: %s", data)
            return data
        except Exception as e:
            logger.error("LLMClient error in extract_contador_output: %s", e)
            raise

    def extract_auditor_output(
        self,
        *,
        contador_output: dict,
        raw_transactions: list,
        correction_feedback: str | None = None,
    ) -> dict:
        from app.core.gemini_client import AuditorOutputGemini
        asientos = contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
        asientos_text = "\n".join(
            f"- cuenta={a.get('cuenta_puc', 'N/A')} "
            f"tipo={a.get('tipo_movimiento', 'N/A')} valor={a.get('valor', 0)} "
            f"desc={a.get('descripcion', '')}"
            for a in asientos[:20]
        )
        tx_text = "\n".join(
            f"- fecha={t.get('fecha', 'N/A')} nit_emisor={t.get('nit_emisor', 'N/A')} "
            f"total={t.get('total', 0)} desc={t.get('descripcion', '')}"
            for t in raw_transactions[:10]
        )

        prompt = f"""Eres un auditor contable colombiano (NIIF/DIAN).

Transacciones origen:
{tx_text or '- Sin transacciones en entrada'}

Salida del contador:
- fecha_registro: {contador_output.get('fecha_registro')}
- tipo_documento: {contador_output.get('tipo_documento')}
- total_debitos: {contador_output.get('total_debitos')}
- total_creditos: {contador_output.get('total_creditos')}
- asientos:
{asientos_text or '- Sin asientos'}

Evalua coherencia semantica, soporte documental, riesgo fiscal y calidad de la descripcion.
Devuelve una salida estructurada que incluya obligatoriamente:
- fecha_auditoria (YYYY-MM-DD)
- documento_referencia
- aprobado (bool)
- nivel_riesgo (bajo|medio|alto|critico)
- hallazgos (lista de objetos con codigo AUD-XXX, severidad, descripcion, campo_afectado opcional, recomendacion)
- puntaje_calidad (0-100)
- resumen
Si detectas errores graves, marca aprobado=false y explica claramente en resumen."""

        if correction_feedback:
            prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores de esquema y regenera la auditoria."""

        try:
            response = self._invoke(AuditorOutputGemini, prompt)
            data = self._as_dict(response)
            logger.debug("Auditor output generated: %s", data)
            return data
        except Exception as e:
            logger.error("LLMClient error in extract_auditor_output: %s", e)
            raise

    def justify_tax_analysis(self, tax_amounts: dict, rag_context: str) -> Any:
        from app.core.gemini_client import TaxJustification
        retefuente = tax_amounts.get("retefuente", 0)
        reteica = tax_amounts.get("reteica", 0)
        iva = tax_amounts.get("iva", 0)
        tasa_retefuente = tax_amounts.get("tasa_retefuente", "11%")
        tasa_reteica = tax_amounts.get("tasa_reteica", "0.69%")
        tasa_iva = tax_amounts.get("tasa_iva", "19%")
        tipo_transaccion = tax_amounts.get("tipo_transaccion", "servicios")

        normativa_section = rag_context.strip() if rag_context else "No se encontro normativa en la base vectorial."

        prompt = f"""Eres un experto tributario colombiano.

Esta transaccion de tipo '{tipo_transaccion}' requiere:
- Retefuente: ${retefuente:,.0f} (tasa {tasa_retefuente})
- ReteICA: ${reteica:,.0f} (tasa {tasa_reteica})
- IVA: ${iva:,.0f} (tasa {tasa_iva})

Normativa aplicable:
---
{normativa_section}
---

Confirma si las tasas son correctas, cita articulos y da justificacion breve."""

        try:
            response = self._invoke(TaxJustification, prompt)
            return self._as_model(TaxJustification, response)
        except Exception as e:
            logger.warning("LLMClient.justify_tax_analysis failed (%s) - returning fallback", e)
            return TaxJustification(
                referencias=["Art. 383 ET", "Art. 401 ET", "Art. 477 ET", "Decreto 2048/1992"],
                justificacion=(
                    "Retenciones aplicadas segun tasas vigentes del Estatuto Tributario "
                    "colombiano. Retefuente segun Art. 383 ET para servicios; ReteICA segun "
                    "tarifas municipales; IVA segun Art. 477 ET tarifa general."
                ),
                confirma_tasas=True,
            )

    def compute_tax_rates_from_profile(
        self,
        ciudad: str,
        codigo_ciiu: str,
        iva_responsable: bool,
        rag_context: str,
    ) -> Any:
        from app.core.gemini_client import TaxRateLookup
        regimen_desc = (
            "regimen comun (responsable de IVA)"
            if iva_responsable
            else "regimen simplificado (no responsable de IVA)"
        )
        normativa_section = (
            rag_context.strip()
            if rag_context
            else "No se encontro informacion especifica en la base normativa."
        )

        prompt = f"""Eres un experto en tributacion colombiana.

Empresa:
- Ciudad: {ciudad}
- Codigo CIIU: {codigo_ciiu}
- Regimen: {regimen_desc}

Normativa:
---
{normativa_section}
---

Devuelve tasas como fracciones decimales para:
- tasa_retefuente_servicios
- tasa_retefuente_bienes
- tasa_retefuente_arrendamiento
- tasa_reteica
- tasa_iva_general
Y cita fuentes legales."""

        response = self._as_model(TaxRateLookup, self._invoke(TaxRateLookup, prompt))
        logger.info("Tax rate lookup: ciudad=%s ciiu=%s reteica=%s", ciudad, codigo_ciiu, response.tasa_reteica)
        return response

    def classify_document(self, text_preview: str) -> Any:
        """Classify a document based on its content using LLM."""
        from app.services.doc_classifier import CLASSIFICATION_PROMPT, _ClassificationResponse
        prompt = CLASSIFICATION_PROMPT.format(text_preview=text_preview)
        try:
            return self._invoke(_ClassificationResponse, prompt)
        except Exception as e:
            logger.error("LLMClient.classify_document failed: %s", e)
            raise

    def extract_bank_statement(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, BankStatementContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de este EXTRACTO BANCARIO.

Extrae obligatoriamente: entidad financiera, número de cuenta, tipo de cuenta (corriente/ahorros), titular con NIT, período (fecha inicio y fin), saldo inicial, saldo final, TODOS los movimientos (fecha, descripción, referencia, tipo débito/crédito, valor, saldo posterior), resumen de totales, GMF cobrado (4x1000) si aparece, intereses generados, y retención en la fuente sobre rendimientos.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(BankStatementContent, prompt))

    def extract_tax_declaration(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, TaxDeclarationContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto tributario colombiano. Extrae la información de esta DECLARACIÓN TRIBUTARIA (IVA Formulario 300 o ReteICA).

Extrae obligatoriamente: número de formulario DIAN, período bimestral/anual, NIT del declarante, TODOS los renglones del formulario con sus valores (como dict renglón→valor), saldo a pagar o a favor, y fecha de presentación.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(TaxDeclarationContent, prompt))

    def extract_tax_annex(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Delegates to extract_anexo_iva for backward compatibility."""
        return self.extract_anexo_iva(text, correction_feedback=correction_feedback)

    def extract_auxiliary_ledger(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, AuxiliaryLedgerContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano (PUC/NIIF). Extrae la información de este LIBRO AUXILIAR CONTABLE.

Extrae obligatoriamente: entidad, cuenta principal PUC (código y nombre), período, saldo inicial, TODAS las líneas del auxiliar (fecha, comprobante con tipo y número, NIT tercero, nombre tercero, centro de costo, descripción/detalle, débito, crédito, saldo acumulado), total débitos, total créditos, y saldo final.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(AuxiliaryLedgerContent, prompt))

    def extract_financial_statement(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Legacy dispatcher — routes to the dedicated method based on content."""
        # Try to detect which type it is from a keyword scan before invoking LLM
        lower = text[:2000].lower()
        if any(k in lower for k in ("utilidad", "ingresos", "gastos", "costo de venta", "resultado")):
            return self.extract_estado_resultados(text, correction_feedback=correction_feedback)
        return self.extract_balance_general(text, correction_feedback=correction_feedback)

    def extract_balance_general(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Extract balance general / estado de situación financiera."""
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, BalanceGeneralContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano (NIIF/PUC). Extrae la información de este BALANCE GENERAL (Estado de Situación Financiera).

Extrae obligatoriamente: entidad (NIT, razón social), fecha de corte, marco normativo (NIIF plenas/Pymes/microempresas), activos corrientes y no corrientes con subcategorías y totales, pasivos corrientes y no corrientes con subcategorías y totales, patrimonio descompuesto (capital, reservas, resultados ejercicio, resultados acumulados), totales de activos/pasivos/patrimonio, verificación ecuación contable (activos == pasivos + patrimonio), y lista plana de todas las cuentas PUC con saldos.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(BalanceGeneralContent, prompt))

    def extract_estado_resultados(self, text: str, *, correction_feedback: str | None = None) -> dict:
        """Extract estado de resultados / P&L."""
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, EstadoResultadosContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano (NIIF/PUC). Extrae la información de este ESTADO DE RESULTADOS (Estado de Pérdidas y Ganancias).

Extrae obligatoriamente: entidad (NIT, razón social), período (fecha inicio y fin), marco normativo, ingresos ordinarios, otros ingresos, total ingresos, costo de ventas/servicios, utilidad bruta, gastos operacionales (administración y ventas por separado como totales — si el documento da un desglose, suma los componentes y pon el total en el campo correspondiente), utilidad operacional, ingresos y gastos financieros, utilidad antes de impuestos, impuesto de renta, utilidad neta, y lista plana de todas las cuentas PUC clase 4/5/6.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(EstadoResultadosContent, prompt))

    def extract_declaracion_ica(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, DeclaracionICAContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto tributario colombiano especializado en impuestos municipales. Extrae la información de esta DECLARACIÓN DE ICA (Impuesto de Industria y Comercio).

Extrae obligatoriamente: municipio y departamento, período gravable (año, periodicidad, bimestre si aplica), NIT y razón social del declarante, actividades económicas con código CIIU y tarifa en por mil, ingresos brutos del período, deducciones aplicadas (fuera de jurisdicción, exentos, no sujetos, exportaciones), total ingresos gravables, liquidación completa (ICA, avisos y tableros 15%, sobretasa bomberil, retenciones, anticipos, sanciones, intereses, total a pagar), y tipo de declaración (inicial/corrección).

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(DeclaracionICAContent, prompt))

    def extract_autorretencion_ica(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, AutoretencionICAContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto tributario colombiano. Extrae la información de esta DECLARACIÓN DE AUTORRETENCIÓN DE ICA.

Extrae obligatoriamente: municipio, departamento, año, periodicidad (mensual/bimestral), número de período, NIT y razón social del declarante, detalle de autorretenciones por actividad económica (CIIU, tarifa en por mil, base gravable, valor retenido), total autorretenciones, sanciones, intereses, total a pagar, y tipo de declaración.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(AutoretencionICAContent, prompt))

    def extract_anexo_iva(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, AnexoIVAContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto tributario colombiano. Extrae la información de este ANEXO DE IVA.

Extrae obligatoriamente: NIT y razón social del declarante, período, IVA generado desglosado por tarifa (0%, 5%, 19%) con base gravable y valor, total IVA generado, IVA descontable desglosado por concepto (compras gravadas, importaciones, servicios, honorarios) con tarifa y valor, total IVA descontable, saldo a pagar o a favor, y retenciones de IVA practicadas/sufridas.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(AnexoIVAContent, prompt))

    def extract_auxiliar_iva(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, AuxiliarIVAContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de este AUXILIAR DE IVA (libro auxiliar de cuentas de IVA).

Extrae obligatoriamente: entidad, período, para cada cuenta de IVA (código PUC, nombre, tipo IVA: generado/descontable/por pagar/retenido): saldo inicial, TODOS los movimientos (fecha, comprobante, NIT tercero, nombre tercero, factura referencia, descripción, débito, crédito), total débitos, total créditos, saldo final.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(AuxiliarIVAContent, prompt))

    def extract_libro_diario(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, LibroDiarioContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de este LIBRO DIARIO OFICIAL.

Extrae obligatoriamente: entidad, período, y para cada asiento contable: fecha, tipo y número de comprobante, descripción general, líneas con cuenta PUC, nombre de cuenta, NIT tercero, nombre tercero, débito y crédito. También extrae totales globales de débitos y créditos del período.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(LibroDiarioContent, prompt))

    def extract_flujo_caja(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, FlujoDeCajaContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano (NIIF). Extrae la información de este ESTADO DE FLUJOS DE EFECTIVO.

Extrae obligatoriamente: entidad, período, método (directo/indirecto), actividades de operación con detalle línea a línea y flujo neto, actividades de inversión con detalle y flujo neto, actividades de financiación con detalle y flujo neto, variación neta total, efectivo al inicio del período, efectivo al fin del período, y verificación de cuadre (inicio + variación = fin).

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(FlujoDeCajaContent, prompt))

    def extract_cambios_patrimonio(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, CambiosPatrimonioContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano (NIIF). Extrae la información de este ESTADO DE CAMBIOS EN EL PATRIMONIO.

Extrae obligatoriamente: entidad, período, para cada componente patrimonial (capital social, prima, reservas, resultados acumulados, resultado del ejercicio, ORI): saldo inicial, movimientos del período con tipo y valor, saldo final. También extrae el total patrimonio inicio y total patrimonio fin.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(CambiosPatrimonioContent, prompt))

    def extract_notas_financieras(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, NotasEstadosFinancierosContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano (NIIF). Extrae la información de estas NOTAS A LOS ESTADOS FINANCIEROS.

Extrae obligatoriamente: entidad, período, moneda funcional, marco de presentación (NIIF plenas/Pymes/microempresas), hipótesis de negocio en marcha, y para cada nota: número, título, categoría (políticas contables/estimaciones/detalle de partida/contingencias/hechos posteriores/partes relacionadas/impuestos/otra), resumen del contenido clave (máx. 500 palabras), cifras relevantes mencionadas, y políticas contables descritas.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(NotasEstadosFinancierosContent, prompt))

    def extract_comprobante_egreso(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, ComprobanteEgresoContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de este COMPROBANTE DE EGRESO.

Extrae obligatoriamente: número de comprobante, fecha, beneficiario (NIT y razón social), concepto del pago, valor bruto, retenciones practicadas (tipo, base, tarifa, valor para retefuente/reteIVA/reteICA), valor neto a pagar, forma de pago (efectivo/cheque/transferencia), banco y número de cheque si aplica, cuenta contable a debitar, y quién aprobó el pago.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(ComprobanteEgresoContent, prompt))

    def extract_documento_soporte(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, DocumentoSoporteContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de este DOCUMENTO SOPORTE EN ADQUISICIONES A NO OBLIGADOS A FACTURAR (art. 1.6.1.4.12 DUR 1625/2016).

Extrae obligatoriamente: número de documento, fecha, datos del proveedor no obligado a facturar (NIT/cédula, nombre/razón social, régimen), datos de la empresa adquirente, descripción del servicio o bien adquirido, ítems con valores e impuestos, totales, y retenciones practicadas.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(DocumentoSoporteContent, prompt))

    def extract_recibo_caja(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, ReciboCajaContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de este RECIBO DE CAJA.

Extrae obligatoriamente: número de recibo, fecha, quién paga (NIT/cédula y nombre), concepto del pago, valor recibido, forma de pago (efectivo/cheque/transferencia), banco y número de cheque si aplica, y cuenta contable a acreditar.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(ReciboCajaContent, prompt))

    def extract_nomina(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, NominaContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano especializado en nómina. Extrae la información de esta NÓMINA.

Extrae obligatoriamente: empresa (NIT, razón social), período de nómina (inicio y fin), para cada empleado: nombre, cédula, cargo, salario básico, días trabajados, total devengado, deducciones (salud empleado 4%, pensión empleado 4%, retención en la fuente), otras deducciones, total deducciones, neto a pagar. También extrae los totales consolidados y los aportes patronales (salud 8.5%, pensión 12%, ARL, SENA, ICBF, caja de compensación).

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(NominaContent, prompt))

    def extract_conciliacion_bancaria(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, ConciliacionBancariaContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de esta CONCILIACIÓN BANCARIA.

Extrae obligatoriamente: empresa, entidad financiera, número de cuenta, fecha de corte, saldo según extracto bancario, saldo según libros contables, listado de todas las partidas conciliatorias (cheques en tránsito, depósitos en tránsito, notas bancarias no registradas en libros, errores) con descripción/fecha/tipo/valor, y el saldo conciliado resultante.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(ConciliacionBancariaContent, prompt))

    def extract_cuenta_cobro(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, CuentaCobroContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano. Extrae la información de esta CUENTA DE COBRO.

Extrae obligatoriamente: número, fecha, datos del prestador de servicios (cédula/NIT y nombre, persona natural no obligada a facturar), datos del contratante (NIT y razón social), descripción del servicio prestado, valor bruto cobrado, retenciones que debe practicar el contratante (retefuente según actividad, reteICA si aplica), y valor neto a pagar.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(CuentaCobroContent, prompt))

    def extract_planilla_seg_social(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, PlanillaSegSocialContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto contable colombiano especializado en seguridad social. Extrae la información de esta PLANILLA DE APORTES A SEGURIDAD SOCIAL (PILA).

Extrae obligatoriamente: empresa (NIT, razón social), período (YYYY-MM), número de planilla, para cada empleado: nombre, cédula, salario base de cotización, aportes a salud (empleado + empleador), pensión (empleado + empleador), ARL, caja de compensación. También extrae los totales por rubro (salud, pensión, ARL, caja, parafiscales SENA/ICBF) y total a pagar.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(PlanillaSegSocialContent, prompt))

    def extract_recibo_pago_impuesto(self, text: str, *, correction_feedback: str | None = None) -> dict:
        from app.core.gemini_client import GENERAL_EXTRACTION_INSTRUCTIONS, ReciboPagoImpuestoContent
        prompt = f"""{GENERAL_EXTRACTION_INSTRUCTIONS}

Eres un experto tributario colombiano. Extrae la información de este RECIBO DE PAGO DE IMPUESTO.

Extrae obligatoriamente: número de recibo, fecha de pago, tipo de impuesto pagado (IVA/renta/ICA/GMF/retefuente/reteICA/otro), entidad fiscal (DIAN o municipio), NIT y razón social del declarante, período gravable al que corresponde el pago, valor principal, sanciones e intereses si aplica, total pagado, banco donde se realizó el pago, y referencia de pago.

Documento:
---
{text}
---"""
        if correction_feedback:
            prompt += f"\n\n=== CORRECCIÓN REQUERIDA ===\n{correction_feedback}\nCorrige los errores y vuelve a extraer."
        return self._as_dict(self._invoke(ReciboPagoImpuestoContent, prompt))


    # ------------------------------------------------------------------
    # Reportero: Financial analysis methods
    # ------------------------------------------------------------------

    def generate_financial_analysis(
        self,
        financial_data: dict,
        rag_context: str,
        system_prompt: str,
    ) -> dict:
        """Generate a comprehensive financial analysis.

        Args:
            financial_data: Dict with balance_summary, pnl_summary, ratios,
                            monthly_trends, predicciones_numericas, top_accounts, etc.
            rag_context: Normative RAG context string.
            system_prompt: The reportero system prompt constant.

        Returns:
            Dict from ReporteroAnalysisGemini structured output.
        """
        import json
        from app.core.gemini_client import ReporteroAnalysisGemini

        prompt = f"""{system_prompt}

=== DATOS FINANCIEROS A ANALIZAR ===
{json.dumps(financial_data, ensure_ascii=False, indent=2, default=str)}

=== CONTEXTO NORMATIVO (RAG) ===
{rag_context if rag_context else "Sin contexto normativo adicional disponible."}

Genera el análisis financiero completo siguiendo la estructura requerida.
Todas las respuestas deben ser en español."""

        try:
            result = self._invoke(ReporteroAnalysisGemini, prompt)
            data = self._as_dict(result)
            logger.info("Reportero financial analysis generated successfully")
            return data
        except Exception as e:
            logger.error("LLM error in generate_financial_analysis: %s", e)
            raise

    def generate_brief_report_analysis(
        self,
        report_type: str,
        report_data: dict,
        rag_context: str,
    ) -> dict:
        """Generate a brief LLM analysis for a specific report type.

        Used when include_analysis=true on individual report endpoints.
        """
        import json
        from app.core.gemini_client import ReporteroBriefAnalysisGemini

        prompt = f"""Eres un Director Financiero experto en contabilidad colombiana (NIIF, PUC, Estatuto Tributario).

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

        try:
            result = self._invoke(ReporteroBriefAnalysisGemini, prompt)
            return self._as_dict(result)
        except Exception as e:
            logger.warning("Brief report analysis failed (non-fatal): %s", e)
            return {"error": f"Análisis LLM no disponible: {e}"}


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """Return the singleton LLMClient instance."""
    return LLMClient()
