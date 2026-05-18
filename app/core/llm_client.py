"""
Primary LLM orchestrator.

Provider priority: OpenAI (primary) → Gemini (fallback) → Groq (last resort).
All extraction methods live here. `gemini_client.py` re-exports this for
backward compatibility.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Iterator, TypeVar

from pydantic import BaseModel

from app.models.ingest_schemas import (
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
    FlujoDeCajaContent,
    LibroDiarioContent,
    NominaContent,
    LiquidacionCesantiasContent,
    NotaCreditoContent,
    NotaDebitoContent,
    NotasEstadosFinancierosContent,
    PlanillaSegSocialContent,
    ReciboCajaContent,
    ReciboPagoImpuestoContent,
    TaxDeclarationContent,
)
from app.core.prompts import ingest, auditor, contador, reportero

from app.models.llm_schemas import (
    CLASSIFICATION_PROMPT,
    AuditorOutput,
    ClassificationResponse,
    ContadorOutput,
    ReporteroBriefAnalysis,
    ReporteroAnalysis,
    TaxJustification,
    TaxRateLookup,
)

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

        # All providers lazy-initialised on first use
        self._openai: Any = None
        self._gemini: Any = None
        self._groq: Any = None

    # ------------------------------------------------------------------
    # Internal fallback chain
    # ------------------------------------------------------------------

    def _get_openai(self):
        if self._openai is None and self._openai_key:
            from app.core.providers.openai_provider import OpenAIProvider

            self._openai = OpenAIProvider()
        return self._openai

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

    def _get_providers(self) -> list[tuple[str, Any]]:
        """Build the ordered provider fallback list (OpenAI → Gemini → Groq)."""
        providers: list[tuple[str, Any]] = []
        openai_provider = self._get_openai()
        if openai_provider is not None:
            providers.append(("OpenAI", openai_provider))
        if self._gemini_key:
            providers.append(("Gemini", self._get_gemini()))
        if self._groq_key:
            providers.append(("Groq", self._get_groq()))
        return providers

    def _invoke(self, schema_cls: type[BaseModel], prompt: str) -> BaseModel:
        """Invoke with OpenAI → Gemini → Groq fallback chain."""
        providers = self._get_providers()

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
    def _as_model(
        model_cls: type[ModelT], response: BaseModel | dict[str, Any]
    ) -> ModelT:
        if isinstance(response, model_cls):
            return response
        if isinstance(response, BaseModel):
            return model_cls.model_validate(response.model_dump(mode="json"))
        return model_cls.model_validate(response)

    # ------------------------------------------------------------------
    # Extraction methods
    # ------------------------------------------------------------------

    def extract_transactions(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Legacy method — kept for backward compatibility. Routes to extract_factura_venta."""
        return self.extract_factura_venta(text, correction_feedback=correction_feedback)

    def extract_factura_venta(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Extract factura de venta (electronic sales invoice, DIAN Res. 000165/2023)."""
        prompt = ingest.factura_venta(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(FacturaVentaContent, prompt))

    def extract_factura_compra(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Extract factura de compra (purchase invoice)."""
        prompt = ingest.factura_compra(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(FacturaCompraContent, prompt))

    def extract_nota_credito(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Extract nota crédito (credit note)."""
        prompt = ingest.nota_credito(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(NotaCreditoContent, prompt))

    def extract_nota_debito(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Extract nota débito (debit note)."""
        prompt = ingest.nota_debito(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(NotaDebitoContent, prompt))

    def extract_contador_output(
        self,
        raw_transactions: list,
        *,
        doc_type: str = "",
        doc_subtype: str = "",
        rag_context: list[dict] | None = None,
        correction_feedback: str | None = None,
        source_taxes: dict | None = None,
        company_context: dict | None = None,
        puc_ingresos_catalog: list[dict] | None = None,
    ) -> dict:
        """Build the contador prompt via the shared `prompts.contador` module
        and invoke the LLM. `doc_subtype` carries the granular frontend doc
        type (e.g. factura_venta) for prompt routing while `doc_type` is the
        normalized contador-enum hint embedded in the prompt.

        ``company_context`` is a dict describing the tenant emisor
        (``nombre``, ``nit``, ``codigo_ciiu``, ``ciudad``, ``iva_responsable``)
        when known. It helps the LLM pick a 4xxx ingreso account that matches
        the actividad económica.

        ``puc_ingresos_catalog`` is a list of ``{codigo, descripcion}`` rows
        loaded from ``cuentas_puc`` for the 4xxx range so the LLM is
        constrained to real PUC codes that already exist in the system.
        """
        prompt = contador.contador_output(
            raw_transactions,
            doc_type=doc_type,
            doc_subtype=doc_subtype,
            rag_context=rag_context,
            correction_feedback=correction_feedback,
            source_taxes=source_taxes,
            company_context=company_context,
            puc_ingresos_catalog=puc_ingresos_catalog,
        )
        try:
            response = self._invoke(ContadorOutput, prompt)
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
        prompt = auditor.auditor_output(
            contador_output=contador_output,
            raw_transactions=raw_transactions,
            correction_feedback=correction_feedback,
        )
        try:
            response = self._invoke(AuditorOutput, prompt)
            data = self._as_dict(response)
            logger.debug("Auditor output generated: %s", data)
            return data
        except Exception as e:
            logger.error("LLMClient error in extract_auditor_output: %s", e)
            raise

    def justify_tax_analysis(self, tax_amounts: dict, rag_context: str) -> Any:
        retefuente = tax_amounts.get("retefuente", 0)
        reteica = tax_amounts.get("reteica", 0)
        iva = tax_amounts.get("iva", 0)
        tasa_retefuente = tax_amounts.get("tasa_retefuente", "11%")
        tasa_reteica = tax_amounts.get("tasa_reteica", "0.69%")
        tasa_iva = tax_amounts.get("tasa_iva", "19%")
        tipo_transaccion = tax_amounts.get("tipo_transaccion", "servicios")

        normativa_section = (
            rag_context.strip()
            if rag_context
            else "No se encontro normativa en la base vectorial."
        )

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
            logger.warning(
                "LLMClient.justify_tax_analysis failed (%s) - returning fallback", e
            )
            return TaxJustification(
                referencias=[
                    "Art. 383 ET",
                    "Art. 401 ET",
                    "Art. 477 ET",
                    "Decreto 2048/1992",
                ],
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
- tasa_reteica (retención ICA aplicada por el agente retenedor)
- tasa_ica (tarifa ICA real del impuesto sobre actividad — puede diferir de reteica)
- tasa_iva_general
Y cita fuentes legales."""

        response = self._as_model(TaxRateLookup, self._invoke(TaxRateLookup, prompt))
        logger.info(
            "Tax rate lookup: ciudad=%s ciiu=%s reteica=%s",
            ciudad,
            codigo_ciiu,
            response.tasa_reteica,
        )
        return response

    def classify_document(self, text_preview: str) -> ClassificationResponse:
        """Classify a document using each provider's classifier-specific model.

        Providers that expose a `classify()` method use a stronger model
        dedicated to classification (e.g., OpenAI uses gpt-4o-mini instead of
        the smaller gpt-4.1-nano used for extraction). This matches the
        pre-refactor doc_classifier behaviour which hardcoded gpt-4o-mini.

        The defensive coercion guards against providers that, in some
        LangChain versions, return a dict or sibling BaseModel instead of
        the requested schema instance.
        """
        prompt = CLASSIFICATION_PROMPT.format(text_preview=text_preview)
        providers = self._get_providers()

        last_exc: Exception | None = None
        failure_trace: list[str] = []
        for idx, (name, provider) in enumerate(providers):
            try:
                classify_fn = getattr(provider, "classify", provider.invoke)
                result = classify_fn(ClassificationResponse, prompt)
                if not isinstance(result, ClassificationResponse):
                    result = ClassificationResponse.model_validate(
                        result.model_dump() if isinstance(result, BaseModel) else result
                    )
                return result
            except Exception as exc:
                last_exc = exc
                failure_trace.append(
                    f"{name}: {exc.__class__.__name__}: {_compact_error_message(exc)}"
                )
                has_next = idx < (len(providers) - 1)
                if not has_next:
                    break
                if _is_quota_error(exc):
                    logger.warning(
                        "%s quota exceeded — falling back for classification",
                        name,
                    )
                else:
                    raise

        trace_summary = " | ".join(failure_trace) if failure_trace else str(last_exc)
        raise RuntimeError(
            f"All configured LLM providers failed for classification. "
            f"Attempts: {trace_summary}"
        )

    def extract_bank_statement(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.bank_statement(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(BankStatementContent, prompt))

    def extract_tax_declaration(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.tax_declaration(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(TaxDeclarationContent, prompt))

    def extract_tax_annex(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Delegates to extract_anexo_iva for backward compatibility."""
        return self.extract_anexo_iva(text, correction_feedback=correction_feedback)

    def extract_auxiliary_ledger(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.auxiliary_ledger(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(AuxiliaryLedgerContent, prompt))

    def extract_financial_statement(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Legacy dispatcher — routes to the dedicated method based on content."""
        prompt = ingest.financial_statement(
            text, correction_feedback=correction_feedback
        )
        # Re-use existing schema dispatch
        lower = text[:2000].lower()
        if any(
            k in lower
            for k in ("utilidad", "ingresos", "gastos", "costo de venta", "resultado")
        ):
            return self._as_dict(self._invoke(EstadoResultadosContent, prompt))
        return self._as_dict(self._invoke(BalanceGeneralContent, prompt))

    def extract_balance_general(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Extract balance general / estado de situación financiera."""
        prompt = ingest.balance_general(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(BalanceGeneralContent, prompt))

    def extract_estado_resultados(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        """Extract estado de resultados / P&L."""
        prompt = ingest.estado_resultados(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(EstadoResultadosContent, prompt))

    def extract_declaracion_ica(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.declaracion_ica(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(DeclaracionICAContent, prompt))

    def extract_autorretencion_ica(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.autorretencion_ica(
            text, correction_feedback=correction_feedback
        )
        return self._as_dict(self._invoke(AutoretencionICAContent, prompt))

    def extract_anexo_iva(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.anexo_iva(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(AnexoIVAContent, prompt))

    def extract_auxiliar_iva(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.auxiliar_iva(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(AuxiliarIVAContent, prompt))

    def extract_libro_diario(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.libro_diario(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(LibroDiarioContent, prompt))

    def extract_flujo_caja(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.flujo_caja(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(FlujoDeCajaContent, prompt))

    def extract_cambios_patrimonio(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.cambios_patrimonio(
            text, correction_feedback=correction_feedback
        )
        return self._as_dict(self._invoke(CambiosPatrimonioContent, prompt))

    def extract_notas_financieras(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.notas_financieras(text, correction_feedback=correction_feedback)
        return self._as_dict(self._invoke(NotasEstadosFinancierosContent, prompt))

    def _ingest_debug_log(
        self, doc_label: str, text: str, prompt: str, data: dict
    ) -> None:
        """Centralised debug logging for ingest extractors.

        Replicates the original ``[CE-DEBUG]`` traces so that every doc_type
        (CE, RC, Nómina, Doc Soporte, etc.) gets the same diagnostic visibility
        when an extracted field looks empty or unexpected. The label rotates so
        log filters can target a specific doc type if needed.
        """
        tag = f"[INGEST-DEBUG:{doc_label}]"
        # Document text preview and prompt length may contain PII (NITs, amounts,
        # names) — log at DEBUG only. Enable LOG_LEVEL=DEBUG for troubleshooting.
        logger.debug(
            "%s markdown length=%d preview=%r",
            tag,
            len(text or ""),
            (text or "")[:1200],
        )
        logger.debug("%s prompt length=%d", tag, len(prompt or ""))
        asientos_doc = data.get("asientos_documento")
        logger.info(
            "%s asientos_documento present=%s count=%s",
            tag,
            asientos_doc is not None,
            len(asientos_doc) if isinstance(asientos_doc, list) else "N/A",
        )
        logger.info(
            "%s extracted keys=%s",
            tag,
            sorted([k for k, v in data.items() if v is not None]),
        )

    def extract_comprobante_egreso(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.comprobante_egreso(
            text, correction_feedback=correction_feedback
        )
        result_obj = self._invoke(ComprobanteEgresoContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("comprobante_egreso", text, prompt, data)
        return data

    def extract_documento_soporte(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.documento_soporte(text, correction_feedback=correction_feedback)
        result_obj = self._invoke(DocumentoSoporteContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("documento_soporte", text, prompt, data)
        return data

    def extract_recibo_caja(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.recibo_caja(text, correction_feedback=correction_feedback)
        result_obj = self._invoke(ReciboCajaContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("recibo_caja", text, prompt, data)
        return data

    def extract_nomina(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.nomina(text, correction_feedback=correction_feedback)
        result_obj = self._invoke(NominaContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("nomina", text, prompt, data)
        return data

    def extract_liquidacion_cesantias(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.liquidacion_cesantias(
            text, correction_feedback=correction_feedback
        )
        result_obj = self._invoke(LiquidacionCesantiasContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("liquidacion_cesantias", text, prompt, data)
        return data

    def extract_conciliacion_bancaria(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.conciliacion_bancaria(
            text, correction_feedback=correction_feedback
        )
        result_obj = self._invoke(ConciliacionBancariaContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("conciliacion_bancaria", text, prompt, data)
        return data

    def extract_cuenta_cobro(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.cuenta_cobro(text, correction_feedback=correction_feedback)
        result_obj = self._invoke(CuentaCobroContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("cuenta_cobro", text, prompt, data)
        return data

    def extract_planilla_seg_social(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.planilla_seg_social(
            text, correction_feedback=correction_feedback
        )
        result_obj = self._invoke(PlanillaSegSocialContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("planilla_seg_social", text, prompt, data)
        return data

    def extract_recibo_pago_impuesto(
        self, text: str, *, correction_feedback: str | None = None
    ) -> dict:
        prompt = ingest.recibo_pago_impuesto(
            text, correction_feedback=correction_feedback
        )
        result_obj = self._invoke(ReciboPagoImpuestoContent, prompt)
        data = self._as_dict(result_obj)
        self._ingest_debug_log("recibo_pago_impuesto", text, prompt, data)
        return data

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
            Dict from ReporteroAnalysis structured output.
        """
        prompt = reportero.reportero_analysis(
            financial_data, rag_context, system_prompt
        )
        try:
            result = self._invoke(ReporteroAnalysis, prompt)
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
        prompt = reportero.reportero_brief(report_type, report_data, rag_context)
        try:
            result = self._invoke(ReporteroBriefAnalysis, prompt)
            return self._as_dict(result)
        except Exception as e:
            logger.warning("Brief report analysis failed (non-fatal): %s", e)
            return {"error": f"Análisis LLM no disponible: {e}"}

    # ------------------------------------------------------------------
    # Chatbot methods
    # ------------------------------------------------------------------

    def classify_chat_intent(self, prompt: str) -> dict:
        """Classify a chat message intent using structured output."""
        from app.models.llm_schemas import ChatIntentClassification

        return self._as_dict(self._invoke(ChatIntentClassification, prompt))

    def generate_chat_response(self, prompt: str) -> dict:
        """Generate a structured (non-streaming) chatbot response."""
        from app.models.llm_schemas import ChatbotResponse

        return self._as_dict(self._invoke(ChatbotResponse, prompt))

    def stream_chat_response(self, prompt: str) -> Iterator[str]:
        """Stream raw text tokens via the provider fallback chain.

        Uses the base model (no structured output) so tokens can be
        yielded progressively.  Fallback only on quota errors, same
        semantics as ``_invoke``.
        """
        providers = self._get_providers()

        last_exc: Exception | None = None
        failure_trace: list[str] = []
        for idx, (name, provider) in enumerate(providers):
            try:
                yield from provider.stream(prompt)
                return
            except Exception as exc:
                last_exc = exc
                failure_trace.append(
                    f"{name}: {exc.__class__.__name__}: {_compact_error_message(exc)}"
                )
                has_next = idx < (len(providers) - 1)
                if not has_next:
                    break
                if _is_quota_error(exc):
                    logger.warning("%s quota exceeded — falling back for stream", name)
                else:
                    raise

        trace_summary = " | ".join(failure_trace) if failure_trace else str(last_exc)
        raise RuntimeError(
            f"All configured LLM providers failed for chat stream. "
            f"Attempts: {trace_summary}"
        )


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """Return the singleton LLMClient instance."""
    return LLMClient()
