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
REAL_EXAMPLES_DIR = ROOT / "storage" / "uploads" / "RealExamples"
SUPPORTED_INPUT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".xml"}


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


def build_runs_from_existing_inputs(input_path: Path) -> list[UploadRun]:
    """Build upload runs from an existing file or directory of files."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    files: list[Path] = []
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(
            [
                p
                for p in input_path.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS
            ]
        )

    if not files:
        raise FileNotFoundError(
            f"No supported input files found in: {input_path} "
            f"(supported: {sorted(SUPPORTED_INPUT_EXTENSIONS)})"
        )

    runs: list[UploadRun] = []
    for file_path in files:
        runs.append(UploadRun(label=file_path.stem, file_path=file_path))
    return runs


def build_via_b_documents(output_dir: Path) -> list[UploadRun]:
    """Generate synthetic first-level financial statement PDFs for Via B testing."""
    output_dir.mkdir(parents=True, exist_ok=True)
    runs: list[UploadRun] = []

    # Balance General
    bg_path = output_dir / "balance_general_2024.pdf"
    _write_pdf(
        bg_path,
        "BALANCE GENERAL",
        [
            "Empresa: Test S.A.S.",
            "NIT: 800999888-2",
            "Periodo: 01/01/2024 - 31/12/2024",
            "ACTIVOS",
            "  1105 Caja: $5,000,000",
            "  1110 Bancos: $45,000,000",
            "  1305 Clientes: $30,000,000",
            "TOTAL ACTIVOS: $80,000,000",
            "PASIVOS",
            "  2205 Proveedores: $20,000,000",
            "TOTAL PASIVOS: $20,000,000",
            "PATRIMONIO",
            "  3105 Capital: $40,000,000",
            "  3605 Utilidad ejercicio: $20,000,000",
            "TOTAL PATRIMONIO: $60,000,000",
        ],
    )
    runs.append(UploadRun("balance_general", bg_path))

    # Estado de Resultados
    er_path = output_dir / "estado_resultados_2024.pdf"
    _write_pdf(
        er_path,
        "ESTADO DE RESULTADOS",
        [
            "Empresa: Test S.A.S.",
            "NIT: 800999888-2",
            "Periodo: 01/01/2024 - 31/12/2024",
            "INGRESOS OPERACIONALES",
            "  4135 Comercio al por mayor: $100,000,000",
            "TOTAL INGRESOS: $100,000,000",
            "GASTOS OPERACIONALES",
            "  5105 Gastos de personal: $50,000,000",
            "  5195 Otros gastos: $30,000,000",
            "TOTAL GASTOS: $80,000,000",
            "UTILIDAD NETA: $20,000,000",
        ],
    )
    runs.append(UploadRun("estado_resultados", er_path))

    # Libro Auxiliar
    la_path = output_dir / "libro_auxiliar_2024.pdf"
    _write_pdf(
        la_path,
        "LIBRO AUXILIAR",
        [
            "Empresa: Test S.A.S.",
            "NIT: 800999888-2",
            "Periodo: 01/01/2024 - 31/12/2024",
            "Cuenta 1105 - Caja",
            "2024-01-15  Ingreso ventas           Deb: 5,000,000   Saldo: 5,000,000",
            "2024-03-01  Pago servicios           Cre: 2,000,000   Saldo: 3,000,000",
            "Cuenta 1110 - Bancos",
            "2024-01-20  Deposito                 Deb: 45,000,000  Saldo: 45,000,000",
        ],
    )
    runs.append(UploadRun("libro_auxiliar", la_path))

    return runs


def run_via_b_pipeline(
    client: httpx.Client,
    args: argparse.Namespace,
) -> int:
    """Upload 3 first-level documents and wait for auto-derivation of second-level docs."""
    output_dir = ROOT / "storage" / "uploads" / "frontend_sim" / "via_b"
    _print_header("VIA B: Uploading first-level financial statements")

    runs = build_via_b_documents(output_dir)
    company_nit = args.company_nit
    base_url = args.base_url

    ensure_company_settings(client, base_url, company_nit, args.city, args.ciiu)

    for run in runs:
        print(f"\n-> Uploading {run.label} from {run.file_path.name}")
        try:
            with run.file_path.open("rb") as f:
                files = {"file": (run.file_path.name, f, "application/pdf")}
                data = {"company_nit": company_nit}
                resp = client.post(
                    f"{base_url}/api/v1/ingest/upload",
                    files=files,
                    data=data,
                    timeout=120,
                )
        except Exception as exc:
            print(f"  [ERR] Upload failed: {exc}")
            run.error = str(exc)
            continue

        if resp.status_code not in (200, 201, 202):
            print(f"  [ERR] Upload failed: {resp.status_code} {resp.text[:200]}")
            run.error = f"HTTP {resp.status_code}"
            continue

        run.ingest_id = resp.json().get("ingest_id", "")
        print(f"  Ingest ID: {run.ingest_id}")

        # Poll until completed
        deadline = time.time() + args.timeout_seconds
        while time.time() < deadline:
            try:
                status_resp = client.get(
                    f"{base_url}/api/v1/ingest/{run.ingest_id}", timeout=10
                )
                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    status = (status_data.get("status") or "").lower()
                    if status == "completed":
                        print(
                            f"  [OK] Ingested as {status_data.get('document_type', '?')}"
                        )
                        run.ingest_status = status
                        break
                    elif status in ("failed", "error"):
                        errors = status_data.get("extraction_errors", "")
                        print(f"  [ERR] Ingest failed: {errors}")
                        run.ingest_status = status
                        run.error = f"ingest status={status}"
                        break
            except Exception:
                pass
            time.sleep(args.poll_seconds)
        else:
            print(f"  [WARN] Ingest timed out")
            run.ingest_status = "timeout"

    print_run_summary(runs)

    # Wait for auto-derivation of second-level documents
    print("\nWaiting for auto-derivation of second-level documents...")
    all_stmts = fetch_all_statements(client, base_url, company_nit, timeout=120)

    print(f"\nTotal stored statements: {len(all_stmts)}")
    for stmt in sorted(all_stmts, key=lambda s: s["statement_type"]):
        mode = stmt.get("source_mode", "?")
        stype = stmt.get("statement_type", "?")
        print(f"  [{mode:30s}] {stype}")

    second_level_types = {"flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros"}
    found_second = {s["statement_type"] for s in all_stmts} & second_level_types
    if len(found_second) == 3:
        print("\n[OK] Via B: All 3 second-level documents derived successfully")
        return 0
    else:
        missing = second_level_types - found_second
        print(f"\n[WARN] Via B: Missing second-level documents: {missing}")
        return 1


def wait_ingest(
    client: httpx.Client, base_url: str, ingest_id: str, timeout_s: int, poll_s: float
) -> dict[str, Any]:
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


def ensure_company_settings(
    client: httpx.Client, base_url: str, nit: str, city: str, ciiu: str
) -> None:
    setup_url = f"{base_url}/api/v1/settings/company/{nit}/setup"
    payload = {
        "nombre": "Empresa Frontend Simulator",
        "ciudad": city,
        "codigo_ciiu": ciiu,
        "iva_responsable": True,
    }
    resp = client.post(setup_url, json=payload, timeout=120)
    resp.raise_for_status()


def wait_process(
    client: httpx.Client, base_url: str, process_id: str, timeout_s: int, poll_s: float
) -> dict[str, Any]:
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
            up = client.post(
                f"{base_url}/api/v1/ingest/upload", files=files, data=data, timeout=120
            )
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

        pr = client.post(
            f"{base_url}/api/v1/process/accounting/{run.ingest_id}", timeout=120
        )
        if pr.status_code == 409:
            detail = (
                pr.json().get("detail")
                if pr.headers.get("content-type", "").startswith("application/json")
                else None
            )
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

        process_status = wait_process(
            client, base_url, run.process_id, timeout_s, poll_s
        )
        run.process_status = process_status.get("status")

        rr = client.get(
            f"{base_url}/api/v1/process/result/{run.process_id}", timeout=120
        )
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


def fetch_all_statements(
    client: httpx.Client, base_url: str, company_nit: str, timeout: float = 60.0
) -> list:
    """Fetch all stored FinancialStatements for the company, waiting for second-level docs."""
    deadline = time.time() + timeout
    second_level = {"flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros"}

    while time.time() < deadline:
        try:
            resp = client.get(
                f"{base_url}/api/v1/reports/statements",
                params={"company_nit": company_nit},
                timeout=30,
            )
            if resp.status_code == 200:
                stmts = resp.json()
                found_second = {s["statement_type"] for s in stmts} & second_level
                if len(found_second) >= 3:
                    return stmts
        except Exception:
            pass
        time.sleep(2)

    # Return whatever exists even if incomplete
    try:
        resp = client.get(
            f"{base_url}/api/v1/reports/statements",
            params={"company_nit": company_nit},
            timeout=30,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def fetch_and_print_reports(
    client: httpx.Client,
    base_url: str,
    company_nit: str,
    cuenta_auxiliar: str,
    report_timeout_s: int,
) -> None:
    _print_header("Reportes finales (fuente: BD)")

    rb = client.get(
        f"{base_url}/api/v1/reports/balance",
        params={"company_nit": company_nit},
        timeout=report_timeout_s,
    )
    rb.raise_for_status()
    balance = rb.json()

    rp = client.get(
        f"{base_url}/api/v1/reports/pnl",
        params={"company_nit": company_nit},
        timeout=report_timeout_s,
    )
    rp.raise_for_status()
    pnl = rp.json()

    ra = client.get(
        f"{base_url}/api/v1/books/",
        params={
            "tipo": "auxiliar",
            "cuenta_puc": cuenta_auxiliar,
            "company_nit": company_nit,
        },
        timeout=report_timeout_s,
    )
    ra.raise_for_status()
    auxiliar = ra.json()

    print("Balance general:")
    print(
        json.dumps(
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
        )
    )

    print("\nEstado de resultados:")
    print(
        json.dumps(
            {
                "total_ingresos": pnl.get("total_ingresos"),
                "total_costo_ventas": pnl.get("total_costo_ventas"),
                "total_gastos": pnl.get("total_gastos"),
                "utilidad_bruta": pnl.get("utilidad_bruta"),
                "utilidad_neta": pnl.get("utilidad_neta"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )

    print(f"\nLibro auxiliar (cuenta {cuenta_auxiliar}):")
    preview = auxiliar[:10] if isinstance(auxiliar, list) else auxiliar
    print(json.dumps(preview, ensure_ascii=True, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Frontend simulator for full accounting pipeline"
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="Backend base URL"
    )
    parser.add_argument(
        "--city", default="Bogota", help="City for /settings/company/{nit}/setup"
    )
    parser.add_argument(
        "--ciiu", default="6920", help="CIIU code for /settings/company/{nit}/setup"
    )
    parser.add_argument(
        "--company-nit",
        default="800999888-2",
        help="Company NIT fallback and ingest override",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=240,
        help="Polling timeout per ingest/process",
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=2.0, help="Polling interval"
    )
    parser.add_argument(
        "--source-mode",
        choices=["auto", "demo", "existing", "via-b"],
        default="auto",
        help=(
            "Input source mode. auto=use existing path if it has files, otherwise demos; "
            "demo=always generate demo docs; existing=use --source-path; "
            "via-b=upload 3 first-level PDFs and wait for auto-derivation"
        ),
    )
    parser.add_argument(
        "--source-path",
        default=str(REAL_EXAMPLES_DIR),
        help="Path to a real input file or folder of files when using source-mode existing/auto",
    )
    parser.add_argument(
        "--report-timeout-seconds",
        type=int,
        default=420,
        help="Timeout for final report endpoints (balance/pnl/books)",
    )
    args = parser.parse_args()

    if args.source_mode == "via-b":
        with httpx.Client() as client:
            return run_via_b_pipeline(client, args)

    input_path = Path(args.source_path).expanduser().resolve()
    if args.source_mode == "demo":
        runs = build_demo_documents()
        source_desc = "demo documents generated by script"
    elif args.source_mode == "existing":
        runs = build_runs_from_existing_inputs(input_path)
        source_desc = f"existing inputs from: {input_path}"
    else:
        # auto mode
        try:
            runs = build_runs_from_existing_inputs(input_path)
            source_desc = f"existing inputs from: {input_path}"
        except FileNotFoundError:
            runs = build_demo_documents()
            source_desc = (
                "demo documents generated by script (existing source not available)"
            )

    _print_header("Frontend simulator: carga de documentos y pipeline")
    print(f"Base URL: {args.base_url}")
    print(f"Fuente de entrada: {source_desc}")
    if args.source_mode == "demo" or (
        args.source_mode == "auto" and "demo" in source_desc
    ):
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
            cuenta_aux = (
                txs[0].get("puc_account") or txs[0].get("cuenta_puc") or cuenta_aux
            )

        # If process result does not include company_nit, recover from ingest detail.
        if not company_nit and chosen.ingest_id:
            ingest_detail = wait_ingest(
                client,
                args.base_url,
                chosen.ingest_id,
                args.timeout_seconds,
                args.poll_seconds,
            )
            raw = ingest_detail.get("raw_transactions") or []
            if raw:
                company_nit = raw[0].get("nit_receptor")

        if not company_nit:
            print("\nNo fue posible determinar company_nit para consultar reportes.")
            return 1

        fetch_and_print_reports(
            client,
            args.base_url,
            company_nit,
            cuenta_aux,
            args.report_timeout_seconds,
        )

        # Display all stored financial statements including second-level
        print("\n" + "=" * 60)
        print("SECOND-LEVEL FINANCIAL DOCUMENTS")
        print("=" * 60)
        all_stmts = fetch_all_statements(client, args.base_url, company_nit, timeout=60)
        print(f"Total stored statements: {len(all_stmts)}")
        for stmt in sorted(all_stmts, key=lambda s: s["statement_type"]):
            mode = stmt.get("source_mode", "?")
            stype = stmt.get("statement_type", "?")
            print(f"  [{mode:30s}] {stype}")
        second_level_types = {"flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros"}
        found_second = {s["statement_type"] for s in all_stmts} & second_level_types
        if len(found_second) == 3:
            print("\n[OK] All 3 second-level documents generated successfully")
        else:
            missing = second_level_types - found_second
            print(f"\n[WARN] Missing second-level documents: {missing}")

    print("\nSimulacion completada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
