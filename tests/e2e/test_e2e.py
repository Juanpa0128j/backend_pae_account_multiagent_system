"""
E2E completo — ambos pipelines contra Supabase real.

Cubre cada agente de ambos pipelines validando:
  • Routing del supervisor (agent_log routing_complete)
  • Input del agente    (campos clave en agent_log node_start)
  • Output del agente   (schema Pydantic canónico)
  • Persistencia DB     (filas en Supabase)

Pipeline 1 – Ingesta:
    supervisor → ingesta → validate_output → db_persist

Pipeline 2 – Proceso:
    supervisor → contador → supervisor(validation) →
    tributario → supervisor(validation) →
    auditor    → supervisor(validation) → db_persist

Requisito: DATABASE_URL debe apuntar a Supabase y la BD debe ser accesible.
Si no, todos los tests de este módulo se saltan automáticamente.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("sqlalchemy")
pytest.importorskip("langgraph")
from reportlab.pdfgen import canvas

from app.agents.graph import create_agent_graph, invoke_ingest_pipeline
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.agent_outputs import (
    AuditorOutput,
    ContadorOutput,
    TributarioOutput,
)
from app.models.database import (
    CuentaPUC,
    IngestStatus,
    JournalEntryLine,
    NaturalezaCuenta,
    ProcessStatus,
    TransactionPending,
    TransactionPosted,
)
from app.services import db_service

# ─────────────────────────────────────────────────────────────────────────────
# Dobles de prueba  (sin llamadas reales a LLM ni a LlamaParse)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeDocument:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeLlamaParse:
    """Stub de LlamaCloud/LlamaParse que devuelve texto fijo sin red."""

    def __init__(self, api_key=None, result_type="markdown", verbose=False):
        pass

    def load_data(self, file_path: str) -> list[_FakeDocument]:
        return [
            _FakeDocument(
                "FACTURA DE VENTA\n"
                "Fecha: 2026-03-14\n"
                "NIT Emisor: 900111222\n"
                "NIT Receptor: 800333444\n"
                "Concepto: Servicios de contabilidad E2E todos agentes\n"
                "Total: 2500000\n"
            )
        ]


class FakeGeminiClient:
    """
    Doble determinístico de GeminiClient.
    Todos los outputs cumplen los schemas Pydantic de app.models.agent_outputs.
    """

    class _Justification:
        def __init__(self, referencias, justificacion, confirma_tasas):
            self.referencias = referencias
            self.justificacion = justificacion
            self.confirma_tasas = confirma_tasas

    # ── Ingesta ──────────────────────────────────────────────────────────────
    def _fake_ingest_result(self) -> dict[str, Any]:
        """FacturaVentaContent-compatible dict for the new rich extraction schema."""
        return {
            "consecutivo": "FAC-E2E-ALL-001",
            "fecha_emision": "2026-03-14",
            "forma_pago": "contado",
            "emisor": {
                "razon_social": "PAE Contadores E2E S.A.S.",
                "nit": "900111222",
            },
            "receptor": {
                "razon_social": "Cliente Demo E2E S.A.S.",
                "nit": "800333444",
            },
            "totales": {
                "subtotal": Decimal("2500000"),
                "total_a_pagar": Decimal("2500000"),
            },
            "items": [
                {
                    "descripcion": "Servicio contable integral",
                    "cantidad": 1,
                    "valor_unitario": Decimal("2500000"),
                    "total_item": Decimal("2500000"),
                },
            ],
        }

    def extract_transactions(
        self,
        text: str,
        correction_feedback: str | None = None,
    ) -> dict[str, Any]:
        return self._fake_ingest_result()

    def __getattr__(self, name: str):
        """Fallback for all extract_* methods added during the pipeline upgrade."""
        if name.startswith("extract_"):

            def _method(
                text: str = "", correction_feedback: str | None = None, **_kw
            ) -> dict[str, Any]:  # noqa: ANN001
                return self._fake_ingest_result()

            return _method
        raise AttributeError(name)

    # ── Contador ─────────────────────────────────────────────────────────────
    def extract_contador_output(
        self,
        raw_transactions: list[dict[str, Any]],
        rag_context: list[dict[str, Any]] | None = None,
        correction_feedback: str | None = None,
    ) -> dict[str, Any]:
        tx = raw_transactions[0] if raw_transactions else {}
        total = Decimal(str(tx.get("total") or "0"))
        fecha = str(tx.get("fecha") or date.today().isoformat())
        if "T" in fecha:
            fecha = fecha.split("T", 1)[0]
        return {
            "fecha_registro": fecha,
            "tipo_documento": "factura",
            "descripcion_general": "Registro contable E2E: servicios de contabilidad",
            "asientos": [
                {
                    "cuenta_puc": "5110",
                    "nombre_cuenta": "Honorarios",
                    "tipo_movimiento": "debito",
                    "valor": float(total),
                    "descripcion": "Reconocimiento de gasto profesional E2E",
                },
                {
                    "cuenta_puc": "220505",
                    "nombre_cuenta": "Proveedores Nacionales",
                    "tipo_movimiento": "credito",
                    "valor": float(total),
                    "descripcion": "Cuenta por pagar — proveedor contable E2E",
                },
            ],
            "total_debitos": float(total),
            "total_creditos": float(total),
        }

    # ── Tributario ───────────────────────────────────────────────────────────
    def justify_tax_analysis(
        self,
        tax_amounts: dict[str, Any],
        rag_context: str,
    ) -> "_Justification":
        return self._Justification(
            referencias=[
                "Art. 383 ET — Retefuente servicios 11 %",
                "Art. 401 ET — Retefuente bienes 3 %",
                "Decreto 2048/1992 — ReteICA Cali 0.69 ‰",
                "Art. 477 ET — IVA general 19 %",
            ],
            justificacion=(
                "Tasas colombianas aplicadas correctamente para E2E full-pipeline."
            ),
            confirma_tasas=True,
        )

    def compute_tax_rates_from_profile(
        self, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return {}

    # ── Auditor ──────────────────────────────────────────────────────────────
    def extract_auditor_output(
        self,
        contador_output: dict[str, Any],
        raw_transactions: list[dict[str, Any]],
        correction_feedback: str | None = None,
    ) -> dict[str, Any]:
        return {
            "fecha_auditoria": date.today().isoformat(),
            "documento_referencia": "FAC-E2E-ALL-001",
            "aprobado": True,
            "nivel_riesgo": "bajo",
            "hallazgos": [],
            "puntaje_calidad": 98,
            "resumen": (
                "Asientos contables E2E balanceados y conformes con NIIF colombianas. "
                "Sin hallazgos críticos. Pipeline completo superado."
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de setup
# ─────────────────────────────────────────────────────────────────────────────


def _require_supabase() -> None:
    if "supabase.co" not in settings.database_url:
        pytest.skip("DATABASE_URL no apunta a Supabase — omitiendo E2E real.")
    # Use a short-timeout probe to avoid hanging for 60 s on an unreachable host.
    from sqlalchemy import create_engine

    probe = create_engine(
        settings.database_url,
        echo=False,
        connect_args={"connect_timeout": 2},
    )
    try:
        with probe.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        pytest.skip("Supabase no disponible en este entorno.")
    finally:
        probe.dispose()


def _create_demo_pdf() -> str:
    root = Path(__file__).resolve().parents[1]
    pdf_path = root / "storage" / "uploads" / "test_e2e_todos_agentes.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pdf_path))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 780, "FACTURA — TEST E2E TODOS AGENTES")
    c.setFont("Helvetica", 11)
    lines = [
        "Fecha: 2026-03-14",
        "NIT Emisor: 900111222",
        "NIT Receptor: 800333444",
        "Concepto: Servicios de contabilidad E2E todos agentes",
        "Total: $2.500.000",
    ]
    for offset, line in enumerate(lines):
        c.drawString(50, 740 - offset * 28, line)
    c.showPage()
    c.save()
    return str(pdf_path)


def _ensure_minimum_puc() -> None:
    """Garantiza que los PUC usados por los agentes existan en BD."""
    required = [
        {
            "codigo": "5110",
            "nombre": "Honorarios",
            "clase": 5,
            "grupo": "51",
            "cuenta": None,
            "naturaleza": NaturalezaCuenta.DEBITO,
        },
        {
            "codigo": "220505",
            "nombre": "Proveedores Nacionales",
            "clase": 2,
            "grupo": "22",
            "cuenta": "2205",
            "naturaleza": NaturalezaCuenta.CREDITO,
        },
        {
            "codigo": "240815",
            "nombre": "Retención en la Fuente — Servicios",
            "clase": 2,
            "grupo": "24",
            "cuenta": "2408",
            "naturaleza": NaturalezaCuenta.CREDITO,
        },
        {
            "codigo": "236540",
            "nombre": "ReteICA por pagar",
            "clase": 2,
            "grupo": "23",
            "cuenta": "2365",
            "naturaleza": NaturalezaCuenta.CREDITO,
        },
    ]
    with SessionLocal() as db:
        for acc in required:
            if db_service.validate_puc_exists(db, acc["codigo"]):
                continue
            db.add(
                CuentaPUC(
                    codigo=acc["codigo"],
                    nombre=acc["nombre"],
                    clase=acc["clase"],
                    grupo=acc["grupo"],
                    cuenta=acc["cuenta"],
                    naturaleza=acc["naturaleza"],
                    descripcion=f"Cuenta E2E full-pipeline — {acc['nombre']}",
                    activa=True,
                )
            )

        # Tributario precondition: company tax settings must exist for nit_receptor.
        db_service.upsert_company_settings(
            db,
            nit="800333444",
            data={
                "nombre": "Empresa Demo E2E",
                "ciudad": "Cali",
                "codigo_ciiu": "6920",
                "iva_responsable": True,
                "tasa_retefuente_servicios": Decimal("0.11"),
                "tasa_retefuente_bienes": Decimal("0.03"),
                "tasa_retefuente_arrendamiento": Decimal("0.10"),
                "tasa_reteica": Decimal("0.0069"),
                "tasa_iva_general": Decimal("0.19"),
            },
            commit=False,
        )
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para inspeccionar agent_log
# ─────────────────────────────────────────────────────────────────────────────


def _events_for(agent_log: list[dict], agent: str) -> list[dict]:
    """Filtra entradas del agent_log para un agente concreto."""
    return [e for e in agent_log if e.get("agent") == agent]


def _routing_events(agent_log: list[dict]) -> list[dict]:
    """Devuelve todos los eventos routing_complete del supervisor."""
    return [
        e
        for e in agent_log
        if e.get("agent") == "supervisor" and e.get("event") == "routing_complete"
    ]


def _was_routed_to(agent_log: list[dict], next_agent: str) -> bool:
    return any(
        e.get("details", {}).get("next_agent") == next_agent
        for e in _routing_events(agent_log)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runners que exponen el estado completo del grafo
# ─────────────────────────────────────────────────────────────────────────────


def _invoke_ingest(pdf_path: str) -> dict[str, Any]:
    with (
        patch("app.agents.ingest_agent.LlamaParse", FakeLlamaParse, create=True),
        patch(
            "app.agents.ingest_agent.get_llm_client", return_value=FakeGeminiClient()
        ),
    ):
        return invoke_ingest_pipeline(pdf_path)


def _make_base_state() -> dict[str, Any]:
    """Reproduce the minimal AgentState required by the process pipeline."""
    return {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": None,
        "db_result": None,
        "mode": "ingest",
        "raw_transactions": [],
        "contador_output": {},
        "tributario_output": {},
        "auditor_output": {},
        "company_config": None,
        "process_id": None,
        "pending_transaction_id": None,
        "current_stage": None,
        "agent_log": [],
        "audit_approved": None,
        "audit_rejection_reason": None,
        "audit_decision": None,
        "audit_feedback": None,
    }


def _invoke_process_full_state(
    *,
    ingest_id: str,
    raw_transactions: list[dict],
    pending_transaction_id: str,
    process_id: str,
) -> dict[str, Any]:
    """
    Invoca el pipeline de proceso y devuelve el final_state COMPLETO del grafo
    (no sólo result{}), para poder inspeccionar contador_output,
    tributario_output, auditor_output, agent_log, validation_history, etc.
    """
    fake = FakeGeminiClient()
    graph = create_agent_graph()
    state = _make_base_state()
    state.update(
        {
            "ingest_id": ingest_id,
            "mode": "process",
            "raw_transactions": raw_transactions,
            "process_id": process_id,
            "pending_transaction_id": pending_transaction_id,
            "current_stage": "queued",
        }
    )
    with (
        patch("app.agents.contador_agent.get_llm_client", return_value=fake),
        patch("app.agents.tributario_agent.get_llm_client", return_value=fake),
        patch("app.agents.auditor_agent.get_llm_client", return_value=fake),
    ):
        return dict(graph.invoke(state))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures de módulo — cada pipeline se ejecuta una sola vez
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ingest_result() -> dict[str, Any]:
    """Ejecuta el pipeline de ingesta y devuelve el result dict de invoke_ingest_pipeline."""
    _require_supabase()
    _ensure_minimum_puc()
    pdf_path = _create_demo_pdf()
    return _invoke_ingest(pdf_path)


@pytest.fixture(scope="module")
def process_state(ingest_result: dict[str, Any]) -> dict[str, Any]:
    """
    Extrae el contexto del resultado de ingesta, crea un ProcessJob y
    ejecuta el pipeline de proceso, devolviendo el final_state completo.
    """
    ingest_id = str(ingest_result["ingest_id"])
    db_res = ingest_result.get("db_result") or {}
    pending_id = str(db_res["transaction_pending_id"])
    # result['data'] is now a rich FacturaVentaContent dict, not a list.
    # Build the raw_transaction entry the process pipeline needs.
    data = ingest_result.get("data") or {}
    emisor = (data.get("emisor") or {}) if isinstance(data, dict) else {}
    receptor = (data.get("receptor") or {}) if isinstance(data, dict) else {}
    totales = (data.get("totales") or {}) if isinstance(data, dict) else {}
    raw_transactions: list[dict] = (
        [
            {
                "fecha": (
                    data.get("fecha_emision", "2026-01-01")
                    if isinstance(data, dict)
                    else "2026-01-01"
                ),
                "nit_emisor": emisor.get("nit", ""),
                "nit_receptor": receptor.get("nit", ""),
                "total": float(totales.get("total_a_pagar") or 0),
                "descripcion": (
                    data.get("consecutivo", "") if isinstance(data, dict) else ""
                ),
                "items": data.get("items", []) if isinstance(data, dict) else [],
            }
        ]
        if data
        else []
    )
    assert raw_transactions, "ingest_result no contiene datos en 'data'"

    with SessionLocal() as db:
        job = db_service.create_process_job(db, ingest_id=ingest_id)
        process_id = str(job.id)

    return _invoke_process_full_state(
        ingest_id=ingest_id,
        raw_transactions=raw_transactions,
        pending_transaction_id=pending_id,
        process_id=process_id,
    )


# ═════════════════════════════════════════════════════════════════════════════
# PIPELINE 1 — Ingesta
# supervisor → ingesta → validate_output → db_persist
# ═════════════════════════════════════════════════════════════════════════════


class TestIngestPipeline:
    """Valida cada etapa del pipeline de ingesta contra Supabase real."""

    # ── Supervisor: routing inicial ──────────────────────────────────────────

    def test_supervisor_routing_inicial_a_ingesta(self, ingest_result):
        """El supervisor debe emitir routing_complete {next_agent: ingesta}."""
        log = ingest_result.get("agent_log") or []
        assert _was_routed_to(log, "ingesta"), (
            "Supervisor no enrutó a 'ingesta'. "
            f"Eventos de routing encontrados: {_routing_events(log)}"
        )

    # ── Agente Ingesta: input ────────────────────────────────────────────────

    def test_ingesta_node_start_registrado(self, ingest_result):
        """El nodo ingesta debe registrar un evento node_start en agent_log."""
        log = ingest_result.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "ingesta") if e.get("event") == "node_start"
        ]
        assert starts, "No se encontró node_start de 'ingesta' en agent_log"

    def test_ingesta_node_start_incluye_file_path(self, ingest_result):
        """node_start de ingesta debe registrar el file_path (input al nodo)."""
        log = ingest_result.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "ingesta") if e.get("event") == "node_start"
        ]
        assert starts[0]["details"].get(
            "file_path"
        ), "node_start de ingesta no registró 'file_path' en details"

    # ── Agente Ingesta: output ───────────────────────────────────────────────

    def test_ingesta_status_completed(self, ingest_result):
        """El resultado de la ingesta debe tener status='completed' y sin error."""
        assert ingest_result.get("status") == "completed"
        assert ingest_result.get("error") is None

    def test_ingesta_output_validated_data_cumple_schema_IngestOutput(
        self, ingest_result
    ):
        """validated_data debe contener los datos extraídos del documento."""
        vd = ingest_result.get("validated_data") or {}
        assert (
            vd
        ), "validated_data está vacío; validate_output_node puede no haber corrido"
        # New rich extraction schema: IngestOutput(extra="allow") passes all fields through.
        # Check the content via FacturaVentaContent structure.
        emisor = (vd.get("emisor") or {}) if isinstance(vd, dict) else {}
        totales = (vd.get("totales") or {}) if isinstance(vd, dict) else {}
        assert (
            emisor or totales
        ), f"validated_data no tiene estructura esperada: {list(vd.keys())}"

    def test_ingesta_data_contiene_transaccion_raw(self, ingest_result):
        """result['data'] debe contener el contenido extraído con emisor/receptor correctos."""
        data = ingest_result.get("data") or {}
        assert data, "result['data'] está vacío después de la ingesta"
        # New rich schema: emisor/receptor nested objects
        emisor = data.get("emisor") or {}
        receptor = data.get("receptor") or {}
        assert emisor.get("nit") == "900111222", f"nit emisor incorrecto: {emisor}"
        assert (
            receptor.get("nit") == "800333444"
        ), f"nit receptor incorrecto: {receptor}"
        totales = data.get("totales") or {}
        assert Decimal(str(totales.get("total_a_pagar"))) == Decimal("2500000")

    def test_ingesta_interpretation_complete_registrado(self, ingest_result):
        """ingesta debe emitir interpretation_complete con datos extraídos."""
        log = ingest_result.get("agent_log") or []
        events = [
            e
            for e in _events_for(log, "ingesta")
            if e.get("event") == "interpretation_complete"
        ]
        assert events, "No se encontró interpretation_complete de 'ingesta'"
        details = events[0]["details"]
        # New rich extraction schema: emits doc_type + fields list
        # (legacy extraction emitted tx_count)
        has_data = details.get("tx_count", 0) > 0 or bool(details.get("fields"))
        assert has_data, f"interpretation_complete no contiene datos: {details}"

    # ── validate_output: aceptó el output de ingesta ─────────────────────────

    def test_validate_output_acepto_ingesta(self, ingest_result):
        """validation_history debe tener una entrada de ingesta con is_valid=True."""
        history = ingest_result.get("validation_history") or []
        ingesta_records = [v for v in history if v.get("agent_name") == "ingesta"]
        assert ingesta_records, (
            "validation_history no contiene entrada para 'ingesta' — "
            "validate_output_node puede no haberse ejecutado"
        )
        assert (
            ingesta_records[-1]["is_valid"] is True
        ), f"Última validación de ingesta falló: {ingesta_records[-1]}"

    # ── db_persist (modo ingest): persistencia en Supabase ──────────────────

    def test_db_persist_ingest_crea_ingest_job_completed(self, ingest_result):
        """db_persist debe actualizar IngestJob a status COMPLETED en Supabase."""
        ingest_id = str(ingest_result.get("ingest_id") or "")
        assert ingest_id, "ingest_id no está en el resultado"
        with SessionLocal() as db:
            job = db_service.get_ingest_job(db, ingest_id)
        assert job is not None, f"IngestJob {ingest_id} no encontrado en Supabase"
        assert (
            job.status == IngestStatus.COMPLETED
        ), f"IngestJob.status esperado COMPLETED, obtenido {job.status}"

    def test_db_persist_ingest_crea_transaction_pending(self, ingest_result):
        """db_persist debe crear una fila TransactionPending con NITs correctos."""
        db_res = ingest_result.get("db_result") or {}
        pending_id = str(db_res.get("transaction_pending_id") or "")
        assert pending_id, "transaction_pending_id no está en db_result"
        with SessionLocal() as db:
            row = (
                db.query(TransactionPending)
                .filter(TransactionPending.id == pending_id)
                .first()
            )
        assert row is not None, f"TransactionPending {pending_id} no encontrado"
        assert str(row.nit_emisor) == "900111222"
        assert str(row.nit_receptor) == "800333444"

    def test_db_persist_ingest_crea_transaction_posted(self, ingest_result):
        """db_persist debe crear una TransactionPosted vinculada al pending."""
        db_res = ingest_result.get("db_result") or {}
        pending_id = str(db_res.get("transaction_pending_id") or "")
        assert pending_id, "transaction_pending_id no está en db_result"
        with SessionLocal() as db:
            rows = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.transaction_pending_id == pending_id)
                .order_by(TransactionPosted.created_at.desc())
                .all()
            )
        assert rows, f"No se encontró TransactionPosted para pending_id={pending_id}"


# ═════════════════════════════════════════════════════════════════════════════
# PIPELINE 2 — Proceso
# supervisor → contador → supervisor(val) → tributario → supervisor(val)
#           → auditor   → supervisor(val) → db_persist
# ═════════════════════════════════════════════════════════════════════════════


class TestProcessPipeline:
    """Valida cada agente del pipeline de proceso contra Supabase real."""

    # ── Supervisor: routing inicial a contador ───────────────────────────────

    def test_supervisor_routing_inicial_a_contador(self, process_state):
        """Supervisor debe enrutar a 'contador' al iniciar el pipeline de proceso."""
        log = process_state.get("agent_log") or []
        assert _was_routed_to(
            log, "contador"
        ), f"Supervisor no enrutó a 'contador'. Routing events: {_routing_events(log)}"

    def test_supervisor_no_error_en_inicio(self, process_state):
        """El estado final no debe contener error."""
        assert (
            process_state.get("error") is None
        ), f"Pipeline de proceso terminó con error: {process_state.get('error')}"

    # ── Agente Contador: input ───────────────────────────────────────────────

    def test_contador_node_start_registrado_con_transacciones(self, process_state):
        """Contador debe registrar node_start con tx_count > 0 (input validado)."""
        log = process_state.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "contador") if e.get("event") == "node_start"
        ]
        assert starts, "node_start de 'contador' no encontrado en agent_log"
        assert starts[0]["details"].get("tx_count", 0) > 0, (
            "contador recibió 0 transacciones de entrada — "
            "raw_transactions puede estar vacío en el estado"
        )

    # ── Agente Contador: output ──────────────────────────────────────────────

    def test_contador_output_cumple_schema(self, process_state):
        """contador_output debe validar como ContadorOutput (partida doble, PUC).

        Después de la ejecución de tributario_node, el contador_output es enriquecido
        con asientos de IVA/Retefuente/ReteICA, por lo que total_debitos refleja
        la base original + IVA descontable. El invariante clave es partida doble.
        """
        co = process_state.get("contador_output") or {}
        assert co, "contador_output no encontrado en final_state"
        parsed = ContadorOutput.model_validate(co)
        assert (
            len(parsed.asientos) >= 2
        ), "ContadorOutput debe tener al menos 2 asientos"
        assert parsed.total_debitos == parsed.total_creditos, (
            f"Partida doble violada: débitos={parsed.total_debitos} "
            f"≠ créditos={parsed.total_creditos}"
        )
        # Total debe ser >= monto base de la transacción (2500000)
        assert parsed.total_debitos >= Decimal(
            "2500000"
        ), f"total_debitos={parsed.total_debitos} por debajo del monto base esperado"

    def test_contador_output_codigos_puc_validos(self, process_state):
        """Todos los asientos del contador deben tener códigos PUC de 1-6 dígitos."""
        co = process_state.get("contador_output") or {}
        parsed = ContadorOutput.model_validate(co)
        for asiento in parsed.asientos:
            assert (
                asiento.cuenta_puc.isdigit() and 1 <= len(asiento.cuenta_puc) <= 6
            ), f"PUC inválido: '{asiento.cuenta_puc}'"

    def test_contador_node_complete_registrado(self, process_state):
        """Contador debe registrar node_complete en agent_log."""
        log = process_state.get("agent_log") or []
        completes = [
            e for e in _events_for(log, "contador") if e.get("event") == "node_complete"
        ]
        assert completes, "node_complete de 'contador' no encontrado en agent_log"

    # ── Supervisor: valida contador, enruta a tributario ────────────────────

    def test_supervisor_valida_contador_y_enruta_tributario(self, process_state):
        """Supervisor debe enrutar a 'tributario' tras validar el output del contador."""
        log = process_state.get("agent_log") or []
        assert _was_routed_to(log, "tributario"), (
            f"Supervisor no enrutó a 'tributario'. "
            f"Routing events: {_routing_events(log)}"
        )

    def test_validation_history_incluye_contador_valido(self, process_state):
        """validation_history debe tener una entrada de 'contador' con is_valid=True."""
        history = process_state.get("validation_history") or []
        records = [v for v in history if v.get("agent_name") == "contador"]
        assert records, "validation_history no contiene entrada para 'contador'"
        assert (
            records[-1]["is_valid"] is True
        ), f"Última validación de contador falló: {records[-1]}"

    # ── Agente Tributario: input ─────────────────────────────────────────────

    def test_tributario_node_start_registrado(self, process_state):
        """Tributario debe registrar node_start en agent_log."""
        log = process_state.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "tributario") if e.get("event") == "node_start"
        ]
        assert starts, "node_start de 'tributario' no encontrado en agent_log"

    def test_tributario_node_start_incluye_base_gravable(self, process_state):
        """node_start de tributario debe registrar base_gravable (input clave)."""
        log = process_state.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "tributario") if e.get("event") == "node_start"
        ]
        assert starts[0]["details"].get(
            "base_gravable"
        ), "node_start de tributario no registró 'base_gravable'"

    # ── Agente Tributario: output ────────────────────────────────────────────

    def test_tributario_output_cumple_schema(self, process_state):
        """tributario_output debe validar como TributarioOutput."""
        to = process_state.get("tributario_output") or {}
        assert to, "tributario_output no encontrado en final_state"
        parsed = TributarioOutput.model_validate(to)
        assert parsed.aplica_impuestos is True
        assert parsed.total_impuestos > Decimal("0")

    def test_tributario_output_tiene_referencias_legales(self, process_state):
        """TributarioOutput debe citar al menos una referencia legal colombiana."""
        to = process_state.get("tributario_output") or {}
        parsed = TributarioOutput.model_validate(to)
        assert (
            len(parsed.referencias_legales) >= 1
        ), "TributarioOutput debe citar referencias legales (ET, Decreto, etc.)"

    def test_tributario_output_impuestos_positivos(self, process_state):
        """Cada impuesto liquidado debe tener valor_impuesto > 0."""
        to = process_state.get("tributario_output") or {}
        parsed = TributarioOutput.model_validate(to)
        for imp in parsed.impuestos:
            assert imp.valor_impuesto > Decimal(
                "0"
            ), f"Impuesto {imp.tipo_impuesto} tiene valor_impuesto ≤ 0"

    def test_tributario_output_total_impuestos_consistente(self, process_state):
        """total_impuestos debe ser igual a la suma de todos los valor_impuesto."""
        to = process_state.get("tributario_output") or {}
        parsed = TributarioOutput.model_validate(to)
        calculated = sum(i.valor_impuesto for i in parsed.impuestos)
        assert (
            parsed.total_impuestos == calculated
        ), f"total_impuestos={parsed.total_impuestos} ≠ suma calculada={calculated}"

    def test_tributario_asientos_enriquecidos_no_vacios(self, process_state):
        """asientos_enriquecidos deben contener líneas de IVA/retención."""
        to = process_state.get("tributario_output") or {}
        parsed = TributarioOutput.model_validate(to)
        assert len(parsed.asientos_enriquecidos) >= 2, (
            "asientos_enriquecidos deben incluir al menos 2 líneas "
            "(asientos originales + retenciones/IVA)"
        )

    # ── Supervisor: valida tributario, enruta a auditor ─────────────────────

    def test_supervisor_valida_tributario_y_enruta_auditor(self, process_state):
        """Supervisor debe enrutar a 'auditor' tras validar el output tributario."""
        log = process_state.get("agent_log") or []
        assert _was_routed_to(
            log, "auditor"
        ), f"Supervisor no enrutó a 'auditor'. Routing events: {_routing_events(log)}"

    # ── Agente Auditor: input ────────────────────────────────────────────────

    def test_auditor_node_start_registrado(self, process_state):
        """Auditor debe registrar node_start en agent_log."""
        log = process_state.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "auditor") if e.get("event") == "node_start"
        ]
        assert starts, "node_start de 'auditor' no encontrado en agent_log"

    def test_auditor_recibe_transacciones_raw(self, process_state):
        """node_start de auditor debe reportar tx_count > 0 (input: raw_transactions)."""
        log = process_state.get("agent_log") or []
        starts = [
            e for e in _events_for(log, "auditor") if e.get("event") == "node_start"
        ]
        assert (
            starts[0]["details"].get("tx_count", 0) > 0
        ), "auditor recibió 0 transacciones raw (tx_count=0 en node_start)"

    # ── Agente Auditor: output ───────────────────────────────────────────────

    def test_auditor_output_cumple_schema(self, process_state):
        """auditor_output debe validar como AuditorOutput."""
        ao = process_state.get("auditor_output") or {}
        assert ao, "auditor_output no encontrado en final_state"
        parsed = AuditorOutput.model_validate(ao)
        assert parsed.aprobado is True
        assert parsed.nivel_riesgo.value in ("bajo", "medio", "alto", "critico")
        assert Decimal("0") <= parsed.puntaje_calidad <= Decimal("100")
        assert len(parsed.resumen) >= 10

    def test_auditor_output_consistencia_aprobacion_riesgo(self, process_state):
        """Si aprobado=True, el nivel de riesgo NO puede ser 'alto' o 'critico'."""
        ao = process_state.get("auditor_output") or {}
        parsed = AuditorOutput.model_validate(ao)
        if parsed.aprobado:
            assert parsed.nivel_riesgo.value not in (
                "alto",
                "critico",
            ), "AuditorOutput aprobado no puede tener nivel_riesgo alto/critico"

    def test_auditor_output_sin_hallazgos_criticos_si_aprobado(self, process_state):
        """Si aprobado=True, no debe haber hallazgos con severidad 'critico'."""
        ao = process_state.get("auditor_output") or {}
        parsed = AuditorOutput.model_validate(ao)
        if parsed.aprobado:
            criticos = [h for h in parsed.hallazgos if h.severidad.value == "critico"]
            assert (
                not criticos
            ), f"Se encontraron hallazgos críticos en un audit aprobado: {criticos}"

    def test_auditor_output_puntaje_calidad_alto(self, process_state):
        """Para este escenario E2E sin errores, puntaje_calidad debe ser ≥ 80."""
        ao = process_state.get("auditor_output") or {}
        parsed = AuditorOutput.model_validate(ao)
        assert parsed.puntaje_calidad >= Decimal(
            "80"
        ), f"puntaje_calidad={parsed.puntaje_calidad} por debajo del umbral esperado (80)"

    # ── Supervisor: valida auditor, enruta a db_persist ─────────────────────

    def test_supervisor_valida_auditor_sets_audit_approved(self, process_state):
        """validate_auditor_output_node debe propagar audit_approved=True al estado."""
        assert process_state.get("audit_approved") is True, (
            f"audit_approved={process_state.get('audit_approved')} — "
            "validate_auditor_output_node no propagó el flag"
        )

    def test_supervisor_valida_auditor_y_enruta_db_persist(self, process_state):
        """Supervisor debe enrutar a 'db_persist' tras aprobar el output del auditor."""
        log = process_state.get("agent_log") or []
        assert _was_routed_to(log, "db_persist"), (
            f"Supervisor no enrutó a 'db_persist'. "
            f"Routing events: {_routing_events(log)}"
        )

    def test_validation_history_incluye_auditor_valido(self, process_state):
        """validation_history debe tener una entrada de 'auditor' con is_valid=True."""
        history = process_state.get("validation_history") or []
        records = [v for v in history if v.get("agent_name") == "auditor"]
        assert records, "validation_history no contiene entrada para 'auditor'"
        assert (
            records[-1]["is_valid"] is True
        ), f"Última validación de auditor falló: {records[-1]}"

    # ── db_persist (modo proceso): Supabase ─────────────────────────────────

    def test_db_persist_proceso_sin_error(self, process_state):
        """El estado final del proceso no debe contener error."""
        assert (
            process_state.get("error") is None
        ), f"Pipeline proceso terminó con error: {process_state.get('error')}"

    def test_db_persist_proceso_process_job_completed(self, process_state):
        """ProcessJob debe quedar COMPLETED en Supabase."""
        process_id = str(process_state.get("process_id") or "")
        assert process_id, "process_id no encontrado en final_state"
        with SessionLocal() as db:
            job = db_service.get_process_job(db, process_id)
        assert job is not None, f"ProcessJob {process_id} no encontrado"
        assert (
            job.status == ProcessStatus.COMPLETED
        ), f"ProcessJob.status esperado COMPLETED, obtenido {job.status}"

    def test_db_persist_proceso_crea_transaction_posted(self, process_state):
        """db_persist debe crear TransactionPosted vinculada a la transacción pendiente."""
        db_res = process_state.get("db_result") or {}
        pending_id = str(db_res.get("transaction_pending_id") or "")
        assert pending_id, "transaction_pending_id no está en db_result del proceso"
        with SessionLocal() as db:
            rows = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.transaction_pending_id == pending_id)
                .order_by(TransactionPosted.created_at.desc())
                .all()
            )
        assert rows, f"No se encontró TransactionPosted para pending_id={pending_id}"

    def test_db_persist_proceso_journal_entry_lines_minimas(self, process_state):
        """JournalEntryLines debe tener al menos 2 filas (débito + crédito)."""
        db_res = process_state.get("db_result") or {}
        pending_id = str(db_res.get("transaction_pending_id") or "")
        with SessionLocal() as db:
            posted = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.transaction_pending_id == pending_id)
                .order_by(TransactionPosted.created_at.desc())
                .first()
            )
            assert posted is not None
            count = (
                db.query(JournalEntryLine)
                .filter(JournalEntryLine.transaction_posted_id == posted.id)
                .count()
            )
        assert count >= 2, f"Se esperaban ≥ 2 JournalEntryLines, se encontraron {count}"

    def test_db_persist_proceso_agent_reasoning_en_posted(self, process_state):
        """TransactionPosted.agent_reasoning debe incluir claves 'contador' y 'auditor'."""
        db_res = process_state.get("db_result") or {}
        pending_id = str(db_res.get("transaction_pending_id") or "")
        with SessionLocal() as db:
            posted = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.transaction_pending_id == pending_id)
                .order_by(TransactionPosted.created_at.desc())
                .first()
            )
        assert posted is not None
        reasoning = posted.agent_reasoning or {}
        assert "contador" in reasoning, "agent_reasoning no contiene clave 'contador'"
        assert "auditor" in reasoning, "agent_reasoning no contiene clave 'auditor'"
        assert reasoning["auditor"].get("aprobado") is True

    def test_db_persist_proceso_audit_flags_en_db_result(self, process_state):
        """db_result debe registrar audit_approved=True y nivel_riesgo='bajo'."""
        db_res = process_state.get("db_result") or {}
        assert db_res.get("audit_approved") is True
        assert db_res.get("audit_nivel_riesgo") == "bajo"
        assert isinstance(
            db_res.get("audit_puntaje_calidad"), (int, float, Decimal, str)
        )

    # ── Integridad del agent_log: todos los agentes dejaron traza ────────────

    def test_agent_log_contiene_todos_los_agentes_del_proceso(self, process_state):
        """agent_log debe contener entradas de todos los agentes del pipeline."""
        log = process_state.get("agent_log") or []
        for agent in ("supervisor", "contador", "tributario", "auditor", "db_persist"):
            assert _events_for(
                log, agent
            ), f"No se encontraron entradas en agent_log para '{agent}'"

    def test_agent_log_orden_de_ejecucion(self, process_state):
        """
        Los agentes deben aparecer en el agent_log en el orden correcto del pipeline:
        supervisor → contador → tributario → auditor → db_persist.
        """
        log = process_state.get("agent_log") or []
        ordered_agents = [
            "supervisor",
            "contador",
            "tributario",
            "auditor",
            "db_persist",
        ]
        first_seen = {
            agent: next((i for i, e in enumerate(log) if e.get("agent") == agent), None)
            for agent in ordered_agents
        }
        for agent in ordered_agents:
            assert (
                first_seen[agent] is not None
            ), f"Agente '{agent}' no encontrado en agent_log"
        indices = [first_seen[a] for a in ordered_agents]
        assert indices == sorted(indices), (
            f"Orden de ejecución incorrecto en agent_log: "
            f"{[(a, first_seen[a]) for a in ordered_agents]}"
        )
