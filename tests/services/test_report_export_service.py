from app.services.report_export_service import (
    BalanceSheetExporter,
    CashFlowExporter,
    PnLExporter,
)


def test_balance_pdf_escapes_dynamic_paragraph_text() -> None:
    report = {
        "period_end": "2026-01-31 & cierre <final>",
        "activos": 1000,
        "pasivos": 400,
        "patrimonio": 500,
        "utilidad_neta": 100,
        "cuadre": True,
        "mensaje_cuadre": "OK & validado <sin markup>",
        "activos_detalle": [],
        "pasivos_detalle": [],
        "patrimonio_detalle": [],
    }

    pdf = BalanceSheetExporter.to_pdf(report, company_name="ACME & Co <SAS>")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100


def test_pnl_pdf_escapes_dynamic_paragraph_text() -> None:
    report = {
        "period_start": "2026-01-01 <ini>",
        "period_end": "2026-01-31 & fin",
        "ingresos": [],
        "costo_ventas": [],
        "gastos": [],
        "total_ingresos": 0,
        "total_costo_ventas": 0,
        "total_gastos": 0,
        "utilidad_bruta": 0,
        "utilidad_neta": 0,
    }

    pdf = PnLExporter.to_pdf(report, company_name="ACME <Holdings> & Partners")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100


def test_cashflow_pdf_escapes_dynamic_paragraph_text() -> None:
    report = {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "nota": "Metodo & supuestos <controlados>",
        "cuentas_efectivo": [],
    }

    pdf = CashFlowExporter.to_pdf(report, company_name="Caja & Banco <Principal>")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100
