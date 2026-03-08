"""
Agente Tributario (Tax Specialist)

Role (docs/Diseño de arquitectura de agente):
  - Receives classified journal entries from Contador (ContadorOutput).
  - Calculates Retefuente, ReteICA, IVA using deterministic Python functions.
  - Queries RAG normativo for relevant legal articles.
  - Calls Gemini to validate rates and produce legal justification (TaxJustification).
  - Returns TributarioOutput enriched with tax liability accounts (2365, 2368, 2408).

Tax rates (Colombian legislation):
  - Retefuente: 11% servicios (Art. 383 ET), 3% bienes (Art. 401 ET), 10% arrendamiento
  - ReteICA:    0.69% default Cali (Decreto 2048/1992)
  - IVA:        19% general (Art. 477 ET), 5% reducida, 0% exento
"""

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.agents.agent_utils import append_log
from app.agents.state import AgentState
from app.core.gemini_client import get_gemini_client
from app.services.rag_service import get_rag_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tax rate constants — deterministic, never computed by LLM
# ---------------------------------------------------------------------------

TASA_RETEFUENTE: dict[str, Decimal] = {
    "servicios":     Decimal("0.11"),   # Art. 383 ET — servicios generales
    "bienes":        Decimal("0.03"),   # Art. 401 ET — compras de bienes
    "arrendamiento": Decimal("0.10"),   # Art. 401 ET — arrendamientos
}
TASA_RETEICA_DEFAULT = Decimal("0.0069")   # Tarifa Cali / default municipal

TASA_IVA: dict[str, Decimal] = {
    "general":  Decimal("0.19"),   # Art. 477 ET — tarifa general
    "reducida": Decimal("0.05"),   # Servicios especiales
    "exento":   Decimal("0.00"),   # Bienes/servicios exentos
}

# PUC 5xxx = gastos/servicios; 1xxx = activos; 4xxx = ingresos
PUC_SERVICIOS_START = 5000
PUC_SERVICIOS_END   = 5999

# Tax liability PUC accounts
CUENTA_RETEFUENTE = "2365"   # Retención en la Fuente por pagar
CUENTA_RETEICA    = "2368"   # Retención ICA por pagar
CUENTA_IVA        = "2408"   # IVA descontable / IVA generado


# ---------------------------------------------------------------------------
# Helper: detect transaction type from PUC codes in asientos
# ---------------------------------------------------------------------------

def _detect_transaction_type(asientos: list[dict]) -> str:
    """
    Infer transaction type from debit PUC codes.

    Returns 'servicios', 'bienes', or 'arrendamiento'.
    Defaults to 'servicios' if detection is ambiguous.
    """
    for asiento in asientos:
        if (asiento.get("tipo_movimiento") or "").lower() == "debito":
            puc_raw = str(asiento.get("cuenta_puc", "")).strip()
            if puc_raw.isdigit():
                puc_int = int(puc_raw)
                if PUC_SERVICIOS_START <= puc_int <= PUC_SERVICIOS_END:
                    desc = (asiento.get("descripcion") or "").lower()
                    if "arrendamiento" in desc or "alquiler" in desc:
                        return "arrendamiento"
                    return "servicios"
    return "bienes"


def _has_iva_in_asientos(asientos: list[dict]) -> tuple[bool, Decimal]:
    """Check if IVA (cuenta 2408) already exists in contador asientos."""
    for asiento in asientos:
        puc_raw = str(asiento.get("cuenta_puc", "")).strip()
        if puc_raw == CUENTA_IVA:
            valor = asiento.get("valor", 0)
            return True, Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return False, Decimal("0")


# ---------------------------------------------------------------------------
# Deterministic tax calculators
# ---------------------------------------------------------------------------

def _calc_retefuente(base: Decimal, tipo: str) -> Decimal:
    tasa = TASA_RETEFUENTE.get(tipo, TASA_RETEFUENTE["servicios"])
    return (base * tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_reteica(base: Decimal) -> Decimal:
    return (base * TASA_RETEICA_DEFAULT).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calc_iva(base: Decimal, tarifa: str = "general") -> Decimal:
    tasa = TASA_IVA.get(tarifa, TASA_IVA["general"])
    return (base * tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def tributario_node(state: AgentState) -> AgentState:
    """
    Tributario worker node — replaces Sprint-12 stub.

    1. Reads ContadorOutput from state.
    2. Runs deterministic Colombian tax calculations.
    3. Queries RAG normativo for legal context.
    4. Calls Gemini to validate rates and produce TaxJustification.
    5. Stores TributarioOutput in state["tributario_output"].
    """
    if state.get("error"):
        logger.warning("Tributario: skipping due to upstream error")
        return state

    contador_output = state.get("contador_output") or {}
    if not contador_output:
        state["error"] = "Tributario error: no contador_output in state"
        logger.error(state["error"])
        append_log(state, "tributario", "node_error", {"error": state["error"]})
        return state

    asientos: list[dict] = contador_output.get("asientos", [])
    base_gravable = Decimal(str(contador_output.get("total_debitos", 0)))
    documento_ref = contador_output.get("descripcion_general", "sin referencia")[:100]

    append_log(state, "tributario", "node_start", {
        "base_gravable": str(base_gravable),
        "asientos_count": len(asientos),
    })

    try:
        # ------------------------------------------------------------------
        # Step 1 — Load company tax config from DB (falls back to defaults)
        # ------------------------------------------------------------------
        company_config = state.get("company_config")
        if not company_config:
            nit_receptor = None
            for tx in (state.get("raw_transactions") or []):
                nit_receptor = tx.get("nit_receptor")
                if nit_receptor:
                    break

            if nit_receptor:
                try:
                    from app.core.database import SessionLocal
                    from app.services import db_service as _db_svc
                    _db = SessionLocal()
                    try:
                        row = _db_svc.get_company_settings(_db, nit_receptor)
                        if row:
                            company_config = {
                                "tasa_retefuente_servicios":     float(row.tasa_retefuente_servicios),
                                "tasa_retefuente_bienes":        float(row.tasa_retefuente_bienes),
                                "tasa_retefuente_arrendamiento": float(row.tasa_retefuente_arrendamiento),
                                "tasa_reteica":                  float(row.tasa_reteica),
                                "tasa_iva_general":              float(row.tasa_iva_general),
                                "iva_responsable":               row.iva_responsable,
                            }
                            logger.info(
                                f"Tributario: loaded company settings for NIT {nit_receptor}"
                            )
                        else:
                            logger.info(
                                f"Tributario: no company settings for NIT {nit_receptor} — using defaults"
                            )
                    finally:
                        _db.close()
                except Exception as cfg_err:
                    logger.warning(
                        f"Tributario: could not load company settings ({cfg_err}) — using defaults"
                    )

        append_log(state, "tributario", "config_loaded", {
            "source": "db" if company_config else "defaults",
        })

        # Resolve effective tax rates (DB settings override hardcoded defaults)
        iva_responsable = company_config.get("iva_responsable", True) if company_config else True

        tasa_retefuente_efectiva = {
            "servicios":     Decimal(str(company_config["tasa_retefuente_servicios"]))
                             if company_config else TASA_RETEFUENTE["servicios"],
            "bienes":        Decimal(str(company_config["tasa_retefuente_bienes"]))
                             if company_config else TASA_RETEFUENTE["bienes"],
            "arrendamiento": Decimal(str(company_config["tasa_retefuente_arrendamiento"]))
                             if company_config else TASA_RETEFUENTE["arrendamiento"],
        }
        tasa_reteica_efectiva = (
            Decimal(str(company_config["tasa_reteica"])) if company_config
            else TASA_RETEICA_DEFAULT
        )
        tasa_iva_efectiva = (
            Decimal(str(company_config["tasa_iva_general"])) if company_config
            else TASA_IVA["general"]
        )

        # ------------------------------------------------------------------
        # Step 2 — Determine transaction type from PUC codes
        # ------------------------------------------------------------------
        tipo_transaccion = _detect_transaction_type(asientos)
        logger.info(f"Tributario: detected transaction type = {tipo_transaccion}")

        # ------------------------------------------------------------------
        # Step 3 — Deterministic tax calculations using effective rates
        # ------------------------------------------------------------------
        tasa_retefuente = tasa_retefuente_efectiva.get(tipo_transaccion, tasa_retefuente_efectiva["servicios"])
        retefuente_val = (base_gravable * tasa_retefuente).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        reteica_val = (base_gravable * tasa_reteica_efectiva).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # IVA: capture from asientos if already present, otherwise calculate
        iva_presente, iva_val_existente = _has_iva_in_asientos(asientos)
        if iva_presente:
            iva_val = iva_val_existente
            logger.info(f"Tributario: IVA found in asientos = {iva_val}")
        elif not iva_responsable:
            iva_val = Decimal("0.00")
            logger.info("Tributario: IVA skipped — company is not IVA responsable")
        else:
            iva_val = (base_gravable * tasa_iva_efectiva).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            logger.info(f"Tributario: IVA calculated = {iva_val}")

        logger.info(
            f"Tributario: retefuente={retefuente_val}, reteica={reteica_val}, iva={iva_val}"
        )

        # ------------------------------------------------------------------
        # Step 4 — RAG normativo lookup
        # ------------------------------------------------------------------
        rag_context = ""
        try:
            rag_service = get_rag_service()
            results = rag_service.search_normativo(
                "retención en la fuente servicios Art 383 Estatuto Tributario IVA"
            )
            rag_context = "\n".join(r.content for r in results) if results else ""
            logger.info(f"Tributario: RAG returned {len(results)} normative chunks")
        except Exception as rag_err:
            logger.warning(f"Tributario: RAG lookup failed (continuing): {rag_err}")

        # ------------------------------------------------------------------
        # Step 5 — Gemini justification (structured output)
        # ------------------------------------------------------------------
        tax_amounts = {
            "retefuente":      float(retefuente_val),
            "reteica":         float(reteica_val),
            "iva":             float(iva_val),
            "tasa_retefuente": f"{float(tasa_retefuente) * 100:.1f}%",
            "tasa_reteica":    f"{float(tasa_reteica_efectiva) * 100:.2f}%",
            "tasa_iva":        f"{float(tasa_iva_efectiva) * 100:.0f}%",
            "tipo_transaccion": tipo_transaccion,
        }

        gemini_client = get_gemini_client()
        try:
            justification = gemini_client.justify_tax_analysis(tax_amounts, rag_context)
        except Exception as gemini_err:
            logger.warning(
                f"Tributario: Gemini justification failed (using fallback): {gemini_err}"
            )
            from app.core.gemini_client import TaxJustification as _TaxJustification
            justification = _TaxJustification(
                referencias=["Art. 383 ET", "Art. 401 ET", "Art. 477 ET", "Decreto 2048/1992"],
                justificacion=(
                    "Retenciones aplicadas según tasas vigentes del Estatuto Tributario "
                    "colombiano. Retefuente según Art. 383 ET para servicios; ReteICA según "
                    "tarifas municipales; IVA según Art. 477 ET tarifa general."
                ),
                confirma_tasas=True,
            )
        referencias = justification.referencias
        observaciones = justification.justificacion

        append_log(state, "tributario", "justification_complete", {
            "confirma_tasas": justification.confirma_tasas,
            "referencias_count": len(referencias),
        })

        # ------------------------------------------------------------------
        # Step 5 — Build impuestos list (skip zero-amount entries)
        # ------------------------------------------------------------------
        impuestos = []

        if retefuente_val > 0:
            impuestos.append({
                "tipo_impuesto":     "retefuente",
                "base_gravable":     str(base_gravable),
                "tarifa_porcentaje": str(tasa_retefuente * 100),
                "valor_impuesto":    str(retefuente_val),
                "cuenta_puc":        CUENTA_RETEFUENTE,
            })

        if reteica_val > 0:
            impuestos.append({
                "tipo_impuesto":     "reteica",
                "base_gravable":     str(base_gravable),
                "tarifa_porcentaje": str(tasa_reteica_efectiva * 100),
                "valor_impuesto":    str(reteica_val),
                "cuenta_puc":        CUENTA_RETEICA,
            })

        if iva_val > 0:
            impuestos.append({
                "tipo_impuesto":     "IVA",
                "base_gravable":     str(base_gravable),
                "tarifa_porcentaje": str(tasa_iva_efectiva * 100),
                "valor_impuesto":    str(iva_val),
                "cuenta_puc":        CUENTA_IVA,
            })

        total_impuestos = sum(
            Decimal(i["valor_impuesto"]) for i in impuestos
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        aplica_impuestos = len(impuestos) > 0

        # ------------------------------------------------------------------
        # Step 6 — Build enriched journal entries (add tax liability asientos)
        # ------------------------------------------------------------------
        asientos_enriquecidos = list(asientos)  # copy from contador

        if retefuente_val > 0:
            asientos_enriquecidos.append({
                "cuenta_puc":       CUENTA_RETEFUENTE,
                "descripcion":      f"Retención en la Fuente por pagar — {referencias[0] if referencias else 'Art. 383 ET'}",
                "tipo_movimiento":  "credito",
                "valor":            str(retefuente_val),
            })

        if reteica_val > 0:
            asientos_enriquecidos.append({
                "cuenta_puc":       CUENTA_RETEICA,
                "descripcion":      "Retención ICA por pagar — Decreto 2048/1992",
                "tipo_movimiento":  "credito",
                "valor":            str(reteica_val),
            })

        if iva_val > 0 and not iva_presente:
            asientos_enriquecidos.append({
                "cuenta_puc":       CUENTA_IVA,
                "descripcion":      "IVA descontable — Art. 477 ET",
                "tipo_movimiento":  "credito",
                "valor":            str(iva_val),
            })

        # ------------------------------------------------------------------
        # Step 7 — Build TributarioOutput-compatible dict
        # ------------------------------------------------------------------
        tributario_output = {
            "fecha_analisis":       date.today().isoformat(),
            "documento_referencia": documento_ref,
            "aplica_impuestos":     aplica_impuestos,
            "impuestos":            impuestos,
            "total_impuestos":      str(total_impuestos),
            "observaciones":        f"{observaciones} | Referencias: {', '.join(referencias)}",
            "asientos_enriquecidos": asientos_enriquecidos,
            "referencias_legales":  referencias,
        }

        state["tributario_output"] = tributario_output
        state["interpreted_data"]  = tributario_output
        state["current_agent"]     = "tributario"
        state["current_stage"]     = "tributario_complete"
        state["correction_feedback"] = None

        # Persist reasoning for final API response
        result = state.get("result") or {}
        agent_reasoning = result.get("agent_reasoning") or {}
        agent_reasoning["tributario"] = observaciones
        result["agent_reasoning"] = agent_reasoning
        state["result"] = result

        append_log(state, "tributario", "node_complete", {
            "aplica_impuestos":  aplica_impuestos,
            "total_impuestos":   str(total_impuestos),
            "impuestos_count":   len(impuestos),
            "tipo_transaccion":  tipo_transaccion,
        })

        logger.info(
            f"Tributario: complete — total_impuestos={total_impuestos}, "
            f"aplica={aplica_impuestos}"
        )
        return state

    except Exception as e:
        state["error"] = f"Tributario error: {str(e)}"
        logger.error(state["error"], exc_info=True)
        append_log(state, "tributario", "node_error", {"error": str(e)})
        return state
