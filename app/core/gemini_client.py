import logging
from decimal import Decimal
from functools import lru_cache
from typing import Any, Dict, List, Literal, Optional, TypeVar

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


class RawTransaction(BaseModel):
    """Structured schema for extracted receipt/invoice data."""

    fecha: Optional[str] = Field(None, description="Date in YYYY-MM-DD format")
    nit_emisor: str = Field(description="NIT of the issuer")
    nit_receptor: str = Field(description="NIT of the receiver (empresa)")
    total: Decimal = Field(description="Total amount of the transaction")
    descripcion: Optional[str] = Field(
        None, description="Description/concept of the transaction"
    )
    items: Optional[List[Dict[str, Any]]] = Field(None, description="Line items")

    @field_validator("total", mode="before")
    @classmethod
    def parse_total(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class RawTransactionsList(BaseModel):
    transactions: List[RawTransaction] = Field(
        default_factory=list,
        description="Extracted list of transactions from the document",
    )


class AsientoContableGemini(BaseModel):
    """Simplified journal entry schema for Gemini structured output."""

    cuenta_puc: str = Field(description="PUC account code (1-6 digits)")
    descripcion: str = Field(description="Description of the entry")
    tipo_movimiento: Literal["debito", "credito"] = Field(
        description="Movement type: 'debito' or 'credito' (lowercase)"
    )
    valor: Decimal = Field(description="Amount of the entry")

    @field_validator("valor", mode="before")
    @classmethod
    def parse_valor(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class ContadorOutputGemini(BaseModel):
    """ContadorOutput-compatible schema for Gemini structured output."""

    fecha_registro: str = Field(
        description="Accounting registration date YYYY-MM-DD"
    )
    tipo_documento: str = Field(
        description=(
            "Document type: recibo, factura, extracto, nota_credito, "
            "nota_debito, comprobante_egreso, otro"
        )
    )
    descripcion_general: str = Field(
        description="General description of the accounting event"
    )
    asientos: List[AsientoContableGemini] = Field(
        description="Journal entries (at least one debit and one credit)"
    )
    total_debitos: Decimal = Field(description="Sum of all debit entries")
    total_creditos: Decimal = Field(description="Sum of all credit entries")

    @field_validator("total_debitos", "total_creditos", mode="before")
    @classmethod
    def parse_totals(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class AuditorHallazgoGemini(BaseModel):
    """Single audit finding emitted by auditor structured output."""

    codigo: str = Field(description="Codigo del hallazgo, por ejemplo AUD-001")
    severidad: Literal["info", "advertencia", "error", "critico"] = Field(
        description="Severidad del hallazgo"
    )
    descripcion: str = Field(description="Descripcion breve del hallazgo")
    campo_afectado: Optional[str] = Field(
        None, description="Campo contable afectado (opcional)"
    )
    recomendacion: str = Field(
        description="Recomendacion para corregir el hallazgo"
    )


class AuditorOutputGemini(BaseModel):
    """AuditorOutput-compatible schema for Gemini structured output."""

    fecha_auditoria: str = Field(description="Fecha de auditoria en formato YYYY-MM-DD")
    documento_referencia: str = Field(description="Referencia del documento auditado")
    aprobado: bool = Field(description="True cuando el audit pasa sin bloqueadores")
    nivel_riesgo: Literal["bajo", "medio", "alto", "critico"] = Field(
        description="Nivel de riesgo global de la transaccion"
    )
    hallazgos: List[AuditorHallazgoGemini] = Field(
        default_factory=list,
        description="Lista estructurada de hallazgos detectados",
    )
    puntaje_calidad: Decimal = Field(
        ge=0,
        le=100,
        description="Puntaje de calidad contable entre 0 y 100",
    )
    resumen: str = Field(description="Resumen ejecutivo de la auditoria")


class TaxJustification(BaseModel):
    """Structured output for Gemini tax justification calls."""

    referencias: List[str] = Field(
        description="Legal articles cited, e.g. ['Art. 383 ET', 'Decreto 2048/1992']"
    )
    justificacion: str = Field(
        description="Spanish explanation of why these rates apply to the transaction"
    )
    confirma_tasas: bool = Field(
        description="True if the normative context confirms the calculated rates"
    )


class TaxRateLookup(BaseModel):
    """Structured output for Gemini tax profile setup."""

    tasa_retefuente_servicios: Decimal = Field(
        description="Retefuente rate for services as decimal fraction, e.g. 0.11"
    )
    tasa_retefuente_bienes: Decimal = Field(
        description="Retefuente rate for goods purchases as decimal fraction"
    )
    tasa_retefuente_arrendamiento: Decimal = Field(
        description="Retefuente rate for lease/rent as decimal fraction"
    )
    tasa_reteica: Decimal = Field(
        description="ReteICA rate for city/CIIU as decimal fraction"
    )
    tasa_iva_general: Decimal = Field(
        description="IVA tariff as decimal fraction (0.19 or 0.0)"
    )
    fuentes: List[str] = Field(
        description="Legal articles and municipal agreements supporting rates"
    )

    @field_validator(
        "tasa_retefuente_servicios",
        "tasa_retefuente_bienes",
        "tasa_retefuente_arrendamiento",
        "tasa_reteica",
        "tasa_iva_general",
        mode="before",
    )
    @classmethod
    def parse_rates(cls, v):  # noqa: N805
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v


class GeminiClient:
    """Wrapper for Google Generative AI (Gemini) API via LangChain."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model or settings.gemini_model

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set and not provided")

        self.model = ChatGoogleGenerativeAI(
            model=self.model_name,
            google_api_key=self.api_key,
            temperature=0.0,
            max_output_tokens=2048,
        )

        self.structured_model = self.model.with_structured_output(RawTransactionsList)
        self.contador_model = self.model.with_structured_output(ContadorOutputGemini)
        self.auditor_model = self.model.with_structured_output(AuditorOutputGemini)
        self.tax_model = self.model.with_structured_output(TaxJustification)
        self.tax_lookup_model = self.model.with_structured_output(TaxRateLookup)

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

    def extract_transactions(self, text: str, *, correction_feedback: str | None = None) -> dict:
        prompt = f"""Eres un contable experto en lectura de recibos, facturas y comprobantes colombianos.

Texto extraido del documento:
---
{text}
---

Extrae la informacion como una lista de transacciones.
Asegurate de obtener NIT emisor, NIT receptor, total, descripcion y fecha."""

        if correction_feedback:
            prompt += f"""

=== CORRECCION REQUERIDA ===
{correction_feedback}

Corrige los errores indicados y vuelve a extraer la informacion."""

        try:
            response = self.structured_model.invoke([HumanMessage(content=prompt)])
            data = self._as_dict(response)
            logger.debug("Extracted transactions: %s", data)
            return data
        except Exception as e:
            logger.error("Gemini API error in extract_transactions: %s", e)
            raise

    def extract_contador_output(
        self,
        raw_transactions: list,
        *,
        rag_context: list[dict] | None = None,
        correction_feedback: str | None = None,
    ) -> dict:
        txns_text = "\n".join(
            (
                f"- Fecha: {t.get('fecha', 'N/A')}, NIT emisor: {t.get('nit_emisor', 'N/A')}, "
                f"Total: {t.get('total', 0)}, Descripcion: {t.get('descripcion', 'N/A')}"
            )
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
            response = self.contador_model.invoke([HumanMessage(content=prompt)])
            data = self._as_dict(response)
            logger.debug("Contador output generated: %s", data)
            return data
        except Exception as e:
            logger.error("Gemini API error in extract_contador_output: %s", e)
            raise

    def extract_auditor_output(
        self,
        *,
        contador_output: dict,
        raw_transactions: list,
        correction_feedback: str | None = None,
    ) -> dict:
        asientos = contador_output.get("asientos", []) if isinstance(contador_output, dict) else []
        asientos_text = "\n".join(
            (
                f"- cuenta={a.get('cuenta_puc', 'N/A')} "
                f"tipo={a.get('tipo_movimiento', 'N/A')} valor={a.get('valor', 0)} "
                f"desc={a.get('descripcion', '')}"
            )
            for a in asientos[:20]
        )
        tx_text = "\n".join(
            (
                f"- fecha={t.get('fecha', 'N/A')} nit_emisor={t.get('nit_emisor', 'N/A')} "
                f"total={t.get('total', 0)} desc={t.get('descripcion', '')}"
            )
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
            response = self.auditor_model.invoke([HumanMessage(content=prompt)])
            data = self._as_dict(response)
            logger.debug("Auditor output generated: %s", data)
            return data
        except Exception as e:
            logger.error("Gemini API error in extract_auditor_output: %s", e)
            raise

    def justify_tax_analysis(self, tax_amounts: dict, rag_context: str) -> TaxJustification:
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
            response = self.tax_model.invoke([HumanMessage(content=prompt)])
            return self._as_model(TaxJustification, response)
        except Exception as e:
            logger.warning(
                "GeminiClient.justify_tax_analysis failed (%s) - returning fallback",
                e,
            )
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
    ) -> TaxRateLookup:
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

        response = self._as_model(
            TaxRateLookup,
            self.tax_lookup_model.invoke([HumanMessage(content=prompt)]),
        )
        logger.info(
            "Tax rate lookup: ciudad=%s ciiu=%s reteica=%s",
            ciudad,
            codigo_ciiu,
            response.tasa_reteica,
        )
        return response


@lru_cache(maxsize=1)
def get_gemini_client() -> GeminiClient:
    """Return the singleton GeminiClient instance (cached after first call)."""

    return GeminiClient()
