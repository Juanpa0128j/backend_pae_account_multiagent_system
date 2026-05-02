"""
E2E validation test against Supabase for ingest + contador + auditor pipeline.

This test validates both:
1) Agent outputs returned by the orchestration layer
2) Persistence side-effects in Supabase tables
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from reportlab.pdfgen import canvas

from app.agents.graph import invoke_ingest_pipeline, invoke_accounting_pipeline
from app.core.config import settings
from app.core.database import SessionLocal, check_db_connection
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


@dataclass
class SupabaseE2EContext:
    pdf_path: str
    ingest_id: str
    pending_id: str
    process_id: str
    raw_transactions: list[dict[str, Any]]


class _FakeParsedDocument:
    def __init__(self, text: str):
        self.text = text


class FakeLlamaParse:
    def __init__(
        self,
        api_key: str | None = None,
        result_type: str = "markdown",
        verbose: bool = False,
    ):
        self.api_key = api_key
        self.result_type = result_type
        self.verbose = verbose

    def load_data(self, file_path: str) -> list[_FakeParsedDocument]:
        _ = file_path
        text = (
            "FACTURA DE VENTA\n"
            "Fecha: 2026-03-07\n"
            "NIT Emisor: 900123456\n"
            "NIT Receptor: 800999888\n"
            "Concepto: Servicios profesionales\n"
            "Total: 1250000\n"
        )
        return [_FakeParsedDocument(text)]


class FakeLLMClient:
    class _Justification:
        def __init__(
            self, referencias: list[str], justificacion: str, confirma_tasas: bool
        ):
            self.referencias = referencias
            self.justificacion = justificacion
            self.confirma_tasas = confirma_tasas

    def __getattr__(self, name: str):
        """Return a stub for any extract_* method not explicitly defined."""
        if name.startswith("extract_"):

            def _stub(text: str = "", correction_feedback: str | None = None, **_kw):
                return {
                    "fecha": "2026-03-07",
                    "nit_emisor": "900123456",
                    "nit_receptor": "800999888",
                    "total": 1_250_000,
                    "descripcion": "Servicios profesionales marzo 2026",
                    "transactions": [
                        {
                            "fecha": "2026-03-07",
                            "nit_emisor": "900123456",
                            "nit_receptor": "800999888",
                            "total": 1_250_000,
                            "descripcion": "Servicios profesionales marzo 2026",
                        }
                    ],
                }

            return _stub
        raise AttributeError(name)

    def extract_transactions(
        self, text: str, correction_feedback: str | None = None
    ) -> dict[str, Any]:
        _ = (text, correction_feedback)
        tx = {
            "fecha": "2026-03-07",
            "nit_emisor": "900123456",
            "nit_receptor": "800999888",
            "total": 1_250_000,
            "descripcion": "Servicios profesionales marzo 2026",
            "items": [
                {"descripcion": "Servicio contable", "cantidad": 1, "valor": 1_250_000}
            ],
        }
        return {
            "fecha": tx["fecha"],
            "monto": tx["total"],
            "concepto": tx["descripcion"],
            "beneficiario": "Proveedor Demo S.A.S.",
            "empresa": "PAE Demo Company",
            "referencia": "FAC-E2E-2026-0001",
            "tipo_documento": "factura",
            "transactions": [tx],
        }

    def extract_contador_output(
        self,
        raw_transactions: list[dict[str, Any]],
        rag_context: list[dict[str, Any]] | None = None,
        correction_feedback: str | None = None,
        doc_type: str = "",
        source_taxes: dict | None = None,
    ) -> dict[str, Any]:
        _ = (rag_context, correction_feedback, doc_type, source_taxes)
        tx = raw_transactions[0] if raw_transactions else {}
        total = Decimal(str(tx.get("total") or "0"))
        fecha = str(tx.get("fecha") or datetime.now(timezone.utc).date().isoformat())
        if "T" in fecha:
            fecha = fecha.split("T", 1)[0]
        descripcion = str(tx.get("descripcion") or "Demo")

        return {
            "fecha_registro": fecha,
            "tipo_documento": "factura",
            "descripcion_general": f"Registro contable: {descripcion}",
            "asientos": [
                {
                    "cuenta_puc": "5110",
                    "nombre_cuenta": "Honorarios",
                    "tipo_movimiento": "debito",
                    "valor": float(total),
                    "descripcion": "Reconocimiento de gasto",
                },
                {
                    "cuenta_puc": "220505",
                    "nombre_cuenta": "Proveedores Nacionales",
                    "tipo_movimiento": "credito",
                    "valor": float(total),
                    "descripcion": "Cuenta por pagar",
                },
            ],
            "total_debitos": float(total),
            "total_creditos": float(total),
        }

    def justify_tax_analysis(
        self, tax_amounts: dict[str, Any], rag_context: str
    ) -> _Justification:
        _ = (tax_amounts, rag_context)
        return self._Justification(
            referencias=[
                "Art. 383 ET",
                "Art. 401 ET",
                "Art. 477 ET",
                "Decreto 2048/1992",
            ],
            justificacion="Validacion tributaria para escenario E2E de pruebas.",
            confirma_tasas=True,
        )

    def extract_auditor_output(
        self,
        contador_output: dict[str, Any],
        raw_transactions: list[dict[str, Any]],
        correction_feedback: str | None = None,
    ) -> dict[str, Any]:
        _ = (contador_output, raw_transactions, correction_feedback)
        return {
            "fecha_auditoria": datetime.now(timezone.utc).date().isoformat(),
            "documento_referencia": "FAC-E2E-2026-0001",
            "aprobado": True,
            "nivel_riesgo": "bajo",
            "hallazgos": [],
            "puntaje_calidad": 96,
            "resumen": "Asientos consistentes y balanceados para la prueba E2E.",
        }


def _require_supabase() -> None:
    if "supabase.co" not in settings.database_url:
        pytest.skip("DATABASE_URL no apunta a Supabase.")
    if not check_db_connection():
        pytest.skip("Supabase no disponible en este entorno.")


def _create_demo_pdf() -> str:
    root = Path(__file__).resolve().parents[1]
    pdf_path = root / "storage" / "uploads" / "test_e2e_supabase_pipeline.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(pdf_path))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 780, "FACTURA DE VENTA - TEST E2E SUPABASE")
    c.setFont("Helvetica", 11)
    lines = [
        "Fecha: 2026-03-07",
        "NIT Emisor: 900123456",
        "NIT Receptor: 800999888",
        "Concepto: Servicios profesionales marzo 2026",
        "Total: $1.250.000",
    ]
    y = 740
    for line in lines:
        c.drawString(50, y, line)
        y -= 28
    c.showPage()
    c.save()
    return str(pdf_path)


def _ensure_minimum_puc() -> None:
    required_accounts = [
        {
            "codigo": "5110",
            "nombre": "Honorarios",
            "clase": 5,
            "grupo": "51",
            "cuenta": None,
            "naturaleza": NaturalezaCuenta.DEBITO,
            "descripcion": "Cuenta de gasto para servicios E2E",
        },
        {
            "codigo": "220505",
            "nombre": "Proveedores Nacionales",
            "clase": 2,
            "grupo": "22",
            "cuenta": "2205",
            "naturaleza": NaturalezaCuenta.CREDITO,
            "descripcion": "Cuenta por pagar a proveedores E2E",
        },
    ]

    with SessionLocal() as db:
        for account in required_accounts:
            if db_service.validate_puc_exists(db, account["codigo"]):
                continue
            db.add(
                CuentaPUC(
                    codigo=account["codigo"],
                    nombre=account["nombre"],
                    clase=account["clase"],
                    grupo=account["grupo"],
                    cuenta=account["cuenta"],
                    naturaleza=account["naturaleza"],
                    descripcion=account["descripcion"],
                    activa=True,
                )
            )
        db.commit()


def _run_ingest_pipeline(pdf_path: str) -> dict[str, Any]:
    with (
        patch("app.agents.ingest_agent.LlamaParse", FakeLlamaParse, create=True),
        patch("app.agents.ingest_agent.get_llm_client", return_value=FakeLLMClient()),
    ):
        return invoke_ingest_pipeline(pdf_path)


def _create_context_from_ingest(
    ingest_result: dict[str, Any], pdf_path: str
) -> SupabaseE2EContext:
    ingest_id = str(ingest_result.get("ingest_id") or "")
    assert ingest_id

    db_result = ingest_result.get("db_result") or {}
    pending_id = str(db_result.get("transaction_pending_id") or "")
    assert pending_id

    raw_transactions = ingest_result.get("raw_transactions") or []
    assert isinstance(raw_transactions, list) and raw_transactions

    with SessionLocal() as db:
        process_job = db_service.create_process_job(db, ingest_id=ingest_id)
        process_id = str(process_job.id)

    return SupabaseE2EContext(
        pdf_path=pdf_path,
        ingest_id=ingest_id,
        pending_id=pending_id,
        process_id=process_id,
        raw_transactions=raw_transactions,
    )


def _mock_company_settings() -> MagicMock:
    cs = MagicMock()
    cs.tasa_retefuente_servicios = 0.04
    cs.tasa_retefuente_bienes = 0.025
    cs.tasa_retefuente_arrendamiento = 0.035
    cs.tasa_reteica = 0.00414
    cs.tasa_iva_general = 0.19
    cs.iva_responsable = True
    cs.tasa_ica = 0.00414
    cs.tasa_renta = 0.35
    return cs


def _run_process_pipeline(ctx: SupabaseE2EContext) -> dict[str, Any]:
    fake_client = FakeLLMClient()
    with (
        patch("app.agents.contador_agent.get_llm_client", return_value=fake_client),
        patch("app.agents.tributario_agent.get_llm_client", return_value=fake_client),
        patch("app.agents.auditor_agent.get_llm_client", return_value=fake_client),
        patch(
            "app.services.rag_service.get_rag_service",
            return_value=MagicMock(search_normativo=MagicMock(return_value=[])),
        ),
        patch(
            "app.services.db_service.get_company_settings",
            return_value=_mock_company_settings(),
        ),
    ):
        return invoke_accounting_pipeline(
            ingest_id=ctx.ingest_id,
            raw_transactions=ctx.raw_transactions,
            pending_transaction_id=ctx.pending_id,
            process_id=ctx.process_id,
        )


def test_e2e_ingesta_contador_auditor_and_supabase_persistence() -> None:
    _require_supabase()
    _ensure_minimum_puc()

    pdf_path = _create_demo_pdf()
    ingest_result = _run_ingest_pipeline(pdf_path)

    # Validate INGEST output semantics
    # data is now a structured FacturaVentaContent dict (not a list) — the
    # pipeline returns rich extraction output, raw_transactions live in DB state.
    assert ingest_result.get("status") == "completed"
    assert ingest_result.get("error") is None
    data = ingest_result.get("data")
    assert isinstance(data, dict) and data
    assert data.get("nit_emisor") == "900123456"
    assert data.get("nit_receptor") == "800999888"
    assert Decimal(str(data.get("total", 0))) == Decimal("1250000")

    # Validate ingest DB update
    ingest_id = str(ingest_result.get("ingest_id"))
    pending_id = str(
        (ingest_result.get("db_result") or {}).get("transaction_pending_id")
    )
    assert ingest_id and pending_id

    with SessionLocal() as db:
        ingest_job = db_service.get_ingest_job(db, ingest_id)
        assert ingest_job is not None
        assert ingest_job.status == IngestStatus.COMPLETED

        pending = (
            db.query(TransactionPending)
            .filter(TransactionPending.id == pending_id)
            .first()
        )
        assert pending is not None
        assert str(pending.nit_emisor) == "900123456"

    # Run PROCESS graph and validate agent outputs
    ctx = _create_context_from_ingest(ingest_result, pdf_path)
    process_result = _run_process_pipeline(ctx)

    assert process_result.get("error") is None
    validation_history = process_result.get("validation_history") or []
    agent_names = {
        v.get("agent_name") for v in validation_history if isinstance(v, dict)
    }
    assert "contador" in agent_names

    db_result = process_result.get("db_result") or {}
    posted_id_from_state = str(db_result.get("transaction_posted_id") or "")
    assert db_result.get("transaction_pending_id") == ctx.pending_id
    assert db_result.get("audit_approved") is True
    assert db_result.get("audit_nivel_riesgo") == "bajo"

    # Validate Supabase writes after process
    with SessionLocal() as db:
        process_job = db_service.get_process_job(db, ctx.process_id)
        assert process_job is not None
        assert process_job.status == ProcessStatus.COMPLETED

        posted_rows = (
            db.query(TransactionPosted)
            .filter(TransactionPosted.transaction_pending_id == ctx.pending_id)
            .order_by(TransactionPosted.created_at.desc())
            .all()
        )
        assert posted_rows
        latest_posted = posted_rows[0]
        if posted_id_from_state:
            assert str(latest_posted.id) == posted_id_from_state

        reasoning = latest_posted.agent_reasoning or {}
        assert "contador" in reasoning
        assert "auditor" in reasoning
        assert reasoning["auditor"].get("aprobado") is True

        journal_count = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.transaction_posted_id == latest_posted.id)
            .count()
        )
        assert journal_count >= 2
