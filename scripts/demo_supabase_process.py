"""
End-to-end demo for current graphs using a Supabase PostgreSQL database.

What this script does:
1. Validates DATABASE_URL points to Supabase and DB is reachable.
2. Generates a demo PDF document.
3. Runs full ingest graph (supervisor -> ingesta -> validate_output -> db_persist).
4. Runs full process graph (process_supervisor -> contador -> validate_contador -> db_persist).
5. Verifies final persistence in Supabase.

Run:
    uv run python scripts/demo_supabase_process.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from reportlab.pdfgen import canvas
from sqlalchemy import text

# Allow script execution from repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.graph import invoke_agent, invoke_process_pipeline
from app.core.config import settings
from app.core.database import SessionLocal, check_db_connection
from app.models.database import CuentaPUC, JournalEntryLine, NaturalezaCuenta, TransactionPosted
from app.services import db_service


@dataclass
class DemoContext:
    pdf_path: str
    ingest_id: str
    pending_id: str
    process_id: str
    raw_transactions: list[dict[str, Any]]


class _FakeParsedDocument:
    def __init__(self, text: str):
        self.text = text


class FakeLlamaParse:
    """Deterministic LlamaParse replacement for demo runs."""

    def __init__(self, api_key: str | None = None, result_type: str = "markdown", verbose: bool = False):
        self.api_key = api_key
        self.result_type = result_type
        self.verbose = verbose

    def load_data(self, file_path: str) -> list[_FakeParsedDocument]:
        text = (
            "FACTURA DE VENTA\\n"
            "Fecha: 2026-03-07\\n"
            "NIT Emisor: 900123456\\n"
            "NIT Receptor: 800999888\\n"
            "Concepto: Servicios profesionales\\n"
            "Total: 1250000\\n"
        )
        return [_FakeParsedDocument(text)]


class FakeGeminiClient:
    """Deterministic Gemini replacement for ingest + process demo runs."""

    def extract_transactions(
        self,
        text: str,
        correction_feedback: str | None = None,
    ) -> dict[str, Any]:
        tx = {
            "fecha": "2026-03-07",
            "nit_emisor": "900123456",
            "nit_receptor": "800999888",
            "total": 1250000,
            "descripcion": "Servicios profesionales marzo 2026",
            "items": [{"descripcion": "Servicio contable", "cantidad": 1, "valor": 1250000}],
        }
        # Keep both contracts: IngestOutput schema + legacy fields consumed by db_persist.
        return {
            "transactions": [tx],
            "fecha": tx["fecha"],
            "nit_emisor": tx["nit_emisor"],
            "nit_receptor": tx["nit_receptor"],
            "total": tx["total"],
            "descripcion": tx["descripcion"],
            "items": tx["items"],
        }

    def extract_contador_output(
        self,
        raw_transactions: list[dict[str, Any]],
        correction_feedback: str | None = None,
    ) -> dict[str, Any]:
        tx = raw_transactions[0] if raw_transactions else {}
        total = Decimal(str(tx.get("total") or "0"))
        fecha = str(tx.get("fecha") or datetime.now(timezone.utc).date().isoformat())

        if "T" in fecha:
            fecha = fecha.split("T", 1)[0]

        descripcion = str(tx.get("descripcion") or "Demo Supabase")

        return {
            "fecha_registro": fecha,
            "tipo_documento": "factura",
            "descripcion_general": f"Demo process pipeline: {descripcion}",
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


def _print_step(message: str) -> None:
    print(f"\n[demo] {message}")


def _create_demo_pdf() -> str:
    pdf_path = ROOT / "storage" / "uploads" / "demo_supabase_pipeline.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(pdf_path))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 780, "FACTURA DE VENTA - DEMO SUPABASE")
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


def _validate_supabase_configuration() -> None:
    db_url = settings.database_url
    if "supabase.co" not in db_url:
        raise RuntimeError(
            "DATABASE_URL no parece de Supabase. "
            "Usa la cadena PostgreSQL de Supabase con sslmode=require."
        )

    if not check_db_connection():
        raise RuntimeError("No se pudo conectar a la base de datos. Revisa DATABASE_URL.")

    with SessionLocal() as db:
        # Validate both connectivity and migrated schema.
        db.execute(text("SELECT 1"))
        db.execute(text("SELECT COUNT(*) FROM ingest_jobs"))


def _ensure_demo_puc_accounts() -> None:
    required_accounts = [
        {
            "codigo": "5110",
            "nombre": "Honorarios",
            "clase": 5,
            "grupo": "51",
            "cuenta": None,
            "naturaleza": NaturalezaCuenta.DEBITO,
            "descripcion": "Cuenta demo para gasto por servicios",
        },
        {
            "codigo": "220505",
            "nombre": "Proveedores Nacionales",
            "clase": 2,
            "grupo": "22",
            "cuenta": "2205",
            "naturaleza": NaturalezaCuenta.CREDITO,
            "descripcion": "Cuenta demo para obligaciones con proveedores",
        },
    ]

    with SessionLocal() as db:
        for account in required_accounts:
            exists = db_service.validate_puc_exists(db, account["codigo"])
            if exists:
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
    with patch("app.agents.ingest_agent.LlamaParse", FakeLlamaParse), patch(
        "app.agents.ingest_agent.get_gemini_client", return_value=FakeGeminiClient()
    ):
        return invoke_agent(pdf_path)


def _create_process_context(ingest_result: dict[str, Any], pdf_path: str) -> DemoContext:
    ingest_id = str(ingest_result.get("ingest_id") or "")
    if not ingest_id:
        raise RuntimeError("Ingest pipeline no devolvio ingest_id.")

    db_result = ingest_result.get("db_result") or {}
    pending_id = str(
        db_result.get("transaction_pending_id")
        or db_result.get("pending_transaction_id")
        or ""
    )

    raw_transactions = ingest_result.get("data") or ingest_result.get("raw_transactions") or []
    if isinstance(raw_transactions, dict):
        raw_transactions = raw_transactions.get("transactions") or []

    with SessionLocal() as db:
        if not pending_id:
            staged = db_service.get_transactions_by_ingest(db, ingest_id)
            if not staged:
                raise RuntimeError("Ingest pipeline no persistio transacciones en staging.")

            # Prefer latest pending by creation timestamp if available.
            latest = sorted(
                staged,
                key=lambda t: getattr(t, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            )[-1]
            pending_id = str(getattr(latest, "id", "") or "")

            # If ingest response omitted tx list, recover minimal raw tx payload from staging.
            if not raw_transactions:
                raw_data = getattr(latest, "raw_data", None) or {}
                if isinstance(raw_data, dict) and raw_data:
                    raw_transactions = [raw_data]

        if not pending_id:
            raise RuntimeError("Ingest pipeline no persistio transaction_pending_id.")

        if not raw_transactions:
            raise RuntimeError("Ingest pipeline no devolvio transacciones para process.")

        process_job = db_service.create_process_job(db, ingest_id=ingest_id)

        return DemoContext(
            pdf_path=pdf_path,
            ingest_id=ingest_id,
            pending_id=pending_id,
            process_id=str(process_job.id),
            raw_transactions=raw_transactions,
        )


def _run_process_pipeline(ctx: DemoContext) -> dict[str, Any]:
    with patch("app.agents.contador_agent.get_gemini_client", return_value=FakeGeminiClient()):
        return invoke_process_pipeline(
            ingest_id=ctx.ingest_id,
            raw_transactions=ctx.raw_transactions,
            pending_transaction_id=ctx.pending_id,
            process_id=ctx.process_id,
        )


def _verify_persistence(ctx: DemoContext, process_result: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        tx_ids = process_result.get("transaction_ids") or []
        posted = None

        if tx_ids:
            posted = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.id == str(tx_ids[-1]))
                .first()
            )
        else:
            posted = (
                db.query(TransactionPosted)
                .filter(TransactionPosted.transaction_pending_id == ctx.pending_id)
                .first()
            )

        if not posted:
            return {"posted_found": False}

        line_count = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.transaction_posted_id == posted.id)
            .count()
        )

        process_job = db_service.get_process_job(db, ctx.process_id)
        process_status = getattr(getattr(process_job, "status", None), "value", None)

        return {
            "posted_found": True,
            "transaction_posted_id": posted.id,
            "cuenta_puc": posted.cuenta_puc,
            "journal_lines": line_count,
            "process_status": process_status,
        }


def main() -> int:
    try:
        _print_step("Validando conexion y esquema en Supabase")
        _validate_supabase_configuration()

        _print_step("Asegurando cuentas PUC minimas para el demo")
        _ensure_demo_puc_accounts()

        _print_step("Generando documento PDF de demo")
        pdf_path = _create_demo_pdf()
        print(f"[demo] pdf_path={pdf_path}")

        _print_step("Ejecutando pipeline de INGESTA completo (supervisor -> ingesta -> validate -> db_persist)")
        ingest_result = _run_ingest_pipeline(pdf_path)
        if ingest_result.get("status") != "completed":
            raise RuntimeError(f"Ingest fallo: {ingest_result.get('error')}")

        ctx = _create_process_context(ingest_result, pdf_path)
        print(f"[demo] ingest_id={ctx.ingest_id}")
        print(f"[demo] pending_id={ctx.pending_id}")
        print(f"[demo] process_id={ctx.process_id}")
        print(f"[demo] ingest_validation_steps={len(ingest_result.get('validation_history', []))}")

        _print_step("Ejecutando pipeline de PROCESS completo (process_supervisor -> contador -> validate -> db_persist)")
        result = _run_process_pipeline(ctx)
        print(f"[demo] result.error={result.get('error')}")
        print(f"[demo] process_validation_steps={len(result.get('validation_history', []))}")

        _print_step("Verificando persistencia final en Supabase")
        summary = _verify_persistence(ctx, result)
        if not summary.get("posted_found"):
            raise RuntimeError("No se encontro TransactionPosted para el process ejecutado.")
        if summary.get("process_status") != "completed":
            raise RuntimeError(
                f"ProcessJob no finalizo en completed (status={summary.get('process_status')})."
            )

        print("\n[demo] SUCCESS")
        print(f"[demo] transaction_posted_id={summary['transaction_posted_id']}")
        print(f"[demo] cuenta_puc={summary['cuenta_puc']}")
        print(f"[demo] journal_lines={summary['journal_lines']}")
        print(f"[demo] process_status={summary['process_status']}")
        return 0

    except Exception as exc:
        print("\n[demo] FAILED")
        print(f"[demo] {exc}")
        print("[demo] Sugerencia: revisa DATABASE_URL de Supabase y ejecuta `uv run alembic upgrade head`.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
