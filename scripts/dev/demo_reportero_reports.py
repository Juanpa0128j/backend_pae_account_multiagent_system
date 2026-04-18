"""
Demo: Agente Reportero — 5 tipos de informe financiero con enriquecimiento RAG.

Muestra todos los reportes generados por el Agente Reportero consultando el
Libro Mayor (SQL) y enriqueciendo las referencias con el RAG Normativo.

Cómo ejecutar:
    uv run python scripts/dev/demo_reportero_reports.py

    # Con rango de fechas personalizado:
    uv run python scripts/dev/demo_reportero_reports.py --start 2026-01-01 --end 2026-03-31

Prerequisitos:
    1. DATABASE_URL configurado en .env (Supabase/PostgreSQL)
    2. Migraciones ejecutadas: uv run alembic upgrade head
    3. PUC sembrado: uv run python scripts/seed_puc.py
    4. Normativa sembrada: uv run python scripts/populate_rag.py
    5. HUGGINGFACE_API_KEY en .env (para embeddings RAG)
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.agents.graph import invoke_reporting_pipeline  # noqa: E402

# ─── Report configuration ────────────────────────────────────────────────────

REPORT_CONFIGS = [
    {
        "type": "balance",
        "label": "Balance General",
        "endpoint": "GET /api/v1/reports/balance",
        "key_fields": [
            "activos",
            "pasivos",
            "patrimonio_total",
            "utilidad_neta",
            "cuadre",
            "mensaje_cuadre",
        ],
        "rag_field": "notas_normativas",
    },
    {
        "type": "pnl",
        "label": "Estado de Resultados (P&L)",
        "endpoint": "GET /api/v1/reports/pnl",
        "key_fields": [
            "total_ingresos",
            "total_costo_ventas",
            "total_gastos",
            "utilidad_bruta",
            "utilidad_neta",
        ],
        "rag_field": "notas_normativas",
    },
    {
        "type": "cashflow",
        "label": "Flujo de Caja",
        "endpoint": "GET /api/v1/reports/cashflow",
        "key_fields": ["total_efectivo", "nota"],
        "rag_field": "notas_normativas",
    },
    {
        "type": "iva",
        "label": "Reporte IVA",
        "endpoint": "GET /api/v1/tax/iva",
        "key_fields": ["iva_generado", "iva_descontable", "iva_a_pagar"],
        "rag_field": "referencias",
    },
    {
        "type": "withholdings",
        "label": "Retenciones",
        "endpoint": "GET /api/v1/tax/withholdings",
        "key_fields": ["retencion_en_la_fuente", "retencion_ica", "total_retenciones"],
        "rag_field": "referencias",
    },
]

# ─── Helpers ──────────────────────────────────────────────────────────────────


def fmt_cop(value) -> str:
    """Format a number as Colombian pesos."""
    try:
        return f"$ {float(value):>15,.0f}"
    except (TypeError, ValueError):
        return str(value)


def print_separator(char: str = "─", width: int = 62) -> None:
    print(char * width)


# ─── Main ─────────────────────────────────────────────────────────────────────


def run_demo(start_date: str | None, end_date: str | None) -> None:
    params: dict = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    period_label = f"{start_date or 'inicio'} → {end_date or 'hoy'}"

    print()
    print_separator("═")
    print("  AGENTE REPORTERO — Demo de Informes Financieros")
    print(f"  Período: {period_label}")
    print_separator("═")

    for cfg in REPORT_CONFIGS:
        print()
        print_separator()
        print(f"  {cfg['label'].upper()}")
        print(f"  {cfg['endpoint']}")
        print_separator()

        result = invoke_reporting_pipeline(
            report_type=cfg["type"],
            report_params=params if params else None,
        )

        if result.get("error"):
            print(f"  ❌  ERROR: {result['error']}")
            continue

        report = result.get("report", {})

        # Generated timestamp
        print(f"  Generado: {report.get('generated_at', 'N/A')}")
        print()

        # Key financial figures
        for field in cfg["key_fields"]:
            if field not in report:
                continue
            value = report[field]
            # Format monetary fields
            if isinstance(value, (int, float)) and field not in ("cuadre",):
                print(f"  {field:<28} {fmt_cop(value)}")
            else:
                print(f"  {field:<28} {value}")

        # Detail lines (e.g. cuentas_efectivo for cashflow)
        if cfg["type"] == "cashflow" and report.get("cuentas_efectivo"):
            print()
            print("  Cuentas de efectivo:")
            for cuenta in report["cuentas_efectivo"]:
                print(
                    f"    {cuenta['codigo']} {cuenta['nombre']:<30} {fmt_cop(cuenta['saldo'])}"
                )

        if cfg["type"] == "pnl":
            for section, label in [
                ("ingresos", "Ingresos"),
                ("costo_ventas", "Costo ventas"),
                ("gastos", "Gastos"),
            ]:
                if report.get(section):
                    print()
                    print(f"  {label}:")
                    for cuenta in report[section]:
                        print(
                            f"    {cuenta['codigo']} {cuenta['nombre']:<30} {fmt_cop(cuenta['saldo'])}"
                        )

        # RAG enrichment
        rag_values = report.get(cfg["rag_field"], [])
        if rag_values:
            print()
            print(f"  📚  {cfg['rag_field']} (RAG Normativo):")
            for item in rag_values:
                print(f"       • {item}")
        else:
            print()
            print(f"  ℹ   {cfg['rag_field']}: (RAG no disponible — usando fallback)")

    print()
    print_separator("═")
    print("  Demo completado.")
    print_separator("═")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo del Agente Reportero con RAG")
    parser.add_argument("--start", default=None, help="Fecha inicio YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Fecha fin YYYY-MM-DD")
    args = parser.parse_args()

    run_demo(start_date=args.start, end_date=args.end)
