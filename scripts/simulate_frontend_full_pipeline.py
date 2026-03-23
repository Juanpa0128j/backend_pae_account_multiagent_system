"""
Frontend simulator for end-to-end accounting flow.

What it does:
1. Generates 5 demo documents that emulate frontend uploads:
   - factura_venta
   - factura_compra
   - nota_credito
   - nota_debito
   - extracto_bancario
2. Uploads each document via POST /api/v1/ingest/upload
3. Polls ingest status until completed/failed
4. Ensures company tax settings exist via POST /api/v1/settings/company/{nit}/setup
5. Starts accounting process via POST /api/v1/process/accounting/{ingest_id}
6. Polls process status and fetches process result
7. Reads final reports from DB-backed endpoints:
   - GET /api/v1/reports/balance
   - GET /api/v1/reports/pnl
   - GET /api/v1/books/?tipo=auxiliar

Run:
    uv run python scripts/simulate_frontend_full_pipeline.py

Optional:
    uv run python scripts/simulate_frontend_full_pipeline.py \
      --base-url http://127.0.0.1:8000 \
      --city Medellin \
      --ciiu 6920 \
      --timeout-seconds 240
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "storage" / "uploads" / "frontend_sim"


@dataclass
class UploadRun:
    label: str
    file_path: Path
    ingest_id: str | None = None
    ingest_status: str | None = None
    process_id: str | None = None
    process_status: str | None = None
    process_result: dict[str, Any] | None = None
    error: str | None = None


def _print_header(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _write_pdf(path: Path, title: str, lines: list[str]) -> None:
    try:
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'reportlab'. Run 'uv sync' and retry."
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 780, title)
    c.setFont("Helvetica", 11)
    y = 740
    for line in lines:
        c.drawString(50, y, line)
        y -= 24
    c.showPage()
    c.save()


def _write_extracto_xlsx(path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'openpyxl'. Run 'uv sync' and retry."
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Extracto"
    ws.append(["Fecha", "Descripcion", "Debito", "Credito", "Saldo"])
    ws.append(["2026-03-01", "Saldo inicial", 0, 0, 5000000])
    ws.append(["2026-03-05", "Pago arriendo", 1200000, 0, 3800000])
    ws.append(["2026-03-10", "Ingreso venta", 0, 2500000, 6300000])
    ws.append(["2026-03-15", "Pago nomina", 1800000, 0, 4500000])
    wb.save(path)


def build_demo_documents() -> list[UploadRun]:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    fv = DOCS_DIR / "factura_venta_demo.pdf"
    _write_pdf(
        fv,
        "FACTURA DE VENTA",
        [
            "Consecutivo: FV-2026-001",
            "Fecha: 2026-03-01",
            "NIT Emisor: 900123456-1",
            "NIT Receptor: 800999888-2",
            "Concepto: Servicios de consultoria contable",
            "Total: 2500000",
        ],
    )

    fc = DOCS_DIR / "factura_compra_demo.pdf"
    _write_pdf(
        fc,
        "FACTURA DE COMPRA",
        [
            "Consecutivo: FC-2026-002",
            "Fecha: 2026-03-02",
            "NIT Emisor: 901111222-3",
            "NIT Receptor: 800999888-2",
            "Concepto: Compra de papeleria",
            "Total: 800000",
        ],
    )

    nc = DOCS_DIR / "nota_credito_demo.pdf"
    _write_pdf(
        nc,
        "NOTA CREDITO",
        [
            "Consecutivo: NC-2026-003",
            "Fecha: 2026-03-03",
            "NIT Emisor: 900123456-1",
            "NIT Receptor: 800999888-2",
            "Concepto: Devolucion parcial",
            "Total: 300000",
        ],
    )

    nd = DOCS_DIR / "nota_debito_demo.pdf"
    _write_pdf(
        nd,
        "NOTA DEBITO",
        [
            "Consecutivo: ND-2026-004",
            "Fecha: 2026-03-04",
            "NIT Emisor: 900123456-1",
            "NIT Receptor: 800999888-2",
            "Concepto: Ajuste por interes",
            "Total: 150000",
        ],
    )

    eb = DOCS_DIR / "extracto_bancario_demo.xlsx"
    _write_extracto_xlsx(eb)

    return [
        UploadRun("factura_venta", fv),
        UploadRun("factura_compra", fc),
        UploadRun("nota_credito", nc),
        UploadRun("nota_debito", nd),
        UploadRun("extracto_bancario", eb),
    ]


def wait_ingest(client: httpx.Client, base_url: str, ingest_id: str, timeout_s: int, poll_s: float) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    url = f"{base_url}/api/v1/ingest/{ingest_id}"

    while time.time() < deadline:
        resp = client.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        status = (data.get("status") or "").lower()
        if status in {"completed", "failed", "error"}:
            return data
        time.sleep(poll_s)

    raise TimeoutError(f"Timeout waiting ingest_id={ingest_id}")


def ensure_company_settings(client: httpx.Client, base_url: str, nit: str, city: str, ciiu: str) -> None:
    setup_url = f"{base_url}/api/v1/settings/company/{nit}/setup"
    payload = {
        "nombre": "Empresa Frontend Simulator",
        "ciudad": city,
        "codigo_ciiu": ciiu,
        "iva_responsable": True,
    }
    resp = client.post(setup_url, json=payload, timeout=120)
    resp.raise_for_status()


def wait_process(client: httpx.Client, base_url: str, process_id: str, timeout_s: int, poll_s: float) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    url = f"{base_url}/api/v1/process/status/{process_id}"

    while time.time() < deadline:
        resp = client.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        status = (data.get("status") or "").lower()
        if status in {"completed", "failed", "error", "cancelled"}:
            return data
        time.sleep(poll_s)

    raise TimeoutError(f"Timeout waiting process_id={process_id}")


def run_one_document(
    client: httpx.Client,
    base_url: str,
    run: UploadRun,
    city: str,
    ciiu: str,
    company_nit: str,
    timeout_s: int,
    poll_s: float,
) -> UploadRun:
    try:
        with run.file_path.open("rb") as f:
            files = {"file": (run.file_path.name, f, "application/octet-stream")}
            data = {"company_nit": company_nit}
            up = client.post(f"{base_url}/api/v1/ingest/upload", files=files, data=data, timeout=120)
        up.raise_for_status()
        up_data = up.json()
        run.ingest_id = up_data.get("ingest_id")

        if not run.ingest_id:
            raise RuntimeError(f"No ingest_id returned for {run.label}")

        ingest_detail = wait_ingest(client, base_url, run.ingest_id, timeout_s, poll_s)
        run.ingest_status = ingest_detail.get("status")

        if (run.ingest_status or "").lower() != "completed":
            run.error = f"ingest status={run.ingest_status}"
            return run

        raw_txs = ingest_detail.get("raw_transactions") or []
        nit_receptor = None
        if raw_txs:
            nit_receptor = raw_txs[0].get("nit_receptor")

        if not nit_receptor:
            nit_receptor = company_nit

        ensure_company_settings(client, base_url, nit_receptor, city, ciiu)

        pr = client.post(f"{base_url}/api/v1/process/accounting/{run.ingest_id}", timeout=120)
        if pr.status_code == 409:
            detail = pr.json().get("detail") if pr.headers.get("content-type", "").startswith("application/json") else None
            if isinstance(detail, dict):
                error_code = detail.get("error_code")
                message = detail.get("message") or "Business precondition failed"
                remediation = detail.get("remediation")

                if error_code == "NO_STAGED_TRANSACTIONS":
                    run.process_status = "skipped"
                    run.process_result = {
                        "status": "skipped",
                        "error_code": error_code,
                        "message": message,
                        "remediation": remediation,
                    }
                    run.error = None
                    return run

                run.process_status = "failed"
                run.error = f"process precondition failed ({error_code}): {message}"
                if remediation:
                    run.error = f"{run.error} | remediation: {remediation}"
                return run

            run.process_status = "failed"
            run.error = "process precondition failed (409 Conflict)"
            return run

        pr.raise_for_status()
        pr_data = pr.json()
        run.process_id = pr_data.get("process_id")

        if not run.process_id:
            raise RuntimeError(f"No process_id returned for {run.label}")

        process_status = wait_process(client, base_url, run.process_id, timeout_s, poll_s)
        run.process_status = process_status.get("status")

        rr = client.get(f"{base_url}/api/v1/process/result/{run.process_id}", timeout=120)
        if rr.status_code in (200, 202, 409, 500):
            run.process_result = rr.json()
        else:
            rr.raise_for_status()

        if (run.process_status or "").lower() != "completed":
            run.error = f"process status={run.process_status}"

        return run

    except Exception as exc:
        run.error = str(exc)
        return run


def print_run_summary(runs: list[UploadRun]) -> None:
    _print_header("Resumen carga y pipeline")
    for r in runs:
        print(
            f"- {r.label:18} "
            f"ingest={r.ingest_status or '-':10} "
            f"process={r.process_status or '-':10} "
            f"ingest_id={r.ingest_id or '-'} "
            f"process_id={r.process_id or '-'}"
        )
        if r.error:
            print(f"  error: {r.error}")


def fetch_and_print_reports(client: httpx.Client, base_url: str, company_nit: str, cuenta_auxiliar: str) -> None:
    _print_header("Reportes finales (fuente: BD)")

    rb = client.get(
        f"{base_url}/api/v1/reports/balance",
        params={"company_nit": company_nit},
        timeout=120,
    )
    rb.raise_for_status()
    balance = rb.json()

    rp = client.get(
        f"{base_url}/api/v1/reports/pnl",
        params={"company_nit": company_nit},
        timeout=120,
    )
    rp.raise_for_status()
    pnl = rp.json()

    ra = client.get(
        f"{base_url}/api/v1/books/",
        params={"tipo": "auxiliar", "cuenta_puc": cuenta_auxiliar, "company_nit": company_nit},
        timeout=120,
    )
    ra.raise_for_status()
    auxiliar = ra.json()

    print("Balance general:")
    print(json.dumps(
        {
            "activos": balance.get("activos"),
            "pasivos": balance.get("pasivos"),
            "patrimonio_total": balance.get("patrimonio_total"),
            "utilidad_neta": balance.get("utilidad_neta"),
            "cuadre": balance.get("cuadre"),
            "mensaje_cuadre": balance.get("mensaje_cuadre"),
        },
        ensure_ascii=True,
        indent=2,
    ))

    print("\nEstado de resultados:")
    print(json.dumps(
        {
            "total_ingresos": pnl.get("total_ingresos"),
            "total_costo_ventas": pnl.get("total_costo_ventas"),
            "total_gastos": pnl.get("total_gastos"),
            "utilidad_bruta": pnl.get("utilidad_bruta"),
            "utilidad_neta": pnl.get("utilidad_neta"),
        },
        ensure_ascii=True,
        indent=2,
    ))

    print(f"\nLibro auxiliar (cuenta {cuenta_auxiliar}):")
    preview = auxiliar[:10] if isinstance(auxiliar, list) else auxiliar
    print(json.dumps(preview, ensure_ascii=True, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Frontend simulator for full accounting pipeline")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--city", default="Bogota", help="City for /settings/company/{nit}/setup")
    parser.add_argument("--ciiu", default="6920", help="CIIU code for /settings/company/{nit}/setup")
    parser.add_argument("--company-nit", default="800999888-2", help="Company NIT fallback and ingest override")
    parser.add_argument("--timeout-seconds", type=int, default=240, help="Polling timeout per ingest/process")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Polling interval")
    args = parser.parse_args()

    runs = build_demo_documents()

    _print_header("Frontend simulator: carga de documentos y pipeline")
    print(f"Base URL: {args.base_url}")
    print(f"Documentos generados en: {DOCS_DIR}")

    with httpx.Client() as client:
        completed_with_transactions: list[UploadRun] = []

        for run in runs:
            print(f"\nProcesando {run.label} -> {run.file_path.name}")
            result = run_one_document(
                client=client,
                base_url=args.base_url,
                run=run,
                city=args.city,
                ciiu=args.ciiu,
                company_nit=args.company_nit,
                timeout_s=args.timeout_seconds,
                poll_s=args.poll_seconds,
            )
            if result.process_status and result.process_status.lower() == "completed":
                completed_with_transactions.append(result)

        print_run_summary(runs)

        if not completed_with_transactions:
            print("\nNo hubo procesos completados para consultar reportes.")
            return 1

        # Resolve company_nit and account for auxiliar preview from first completed process result.
        chosen = completed_with_transactions[0]
        txs = (chosen.process_result or {}).get("transactions") or []
        company_nit = None
        cuenta_aux = "5110"

        if txs:
            company_nit = txs[0].get("company_nit") or txs[0].get("nit_receptor")
            cuenta_aux = txs[0].get("puc_account") or txs[0].get("cuenta_puc") or cuenta_aux

        # If process result does not include company_nit, recover from ingest detail.
        if not company_nit and chosen.ingest_id:
            ingest_detail = wait_ingest(client, args.base_url, chosen.ingest_id, args.timeout_seconds, args.poll_seconds)
            raw = ingest_detail.get("raw_transactions") or []
            if raw:
                company_nit = raw[0].get("nit_receptor")

        if not company_nit:
            print("\nNo fue posible determinar company_nit para consultar reportes.")
            return 1

        fetch_and_print_reports(client, args.base_url, company_nit, cuenta_aux)

    print("\nSimulacion completada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
