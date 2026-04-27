from app.services.report_export_service import (
    BalanceSheetExporter,
    CashFlowExporter,
    PnLExporter,
    LibroDiarioExporter,
    LibroAuxiliarExporter,
    CambiosPatrimonioExporter,
    NotasEstadosFinancierosExporter,
)


def _assert_excel_bytes(content: bytes) -> None:
    # XLSX files are ZIP containers and must start with PK signature.
    assert content[:2] == b"PK"
    assert len(content) > 100


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


def test_libro_diario_exporters_generate_valid_outputs() -> None:
    report = {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "transacciones": [
            {
                "fecha": "2026-01-10",
                "cuenta_puc": "1105",
                "cuenta_nombre": "Caja",
                "descripcion": "Ingreso",
                "debito": 1000,
                "credito": 0,
            }
        ],
    }

    pdf = LibroDiarioExporter.to_pdf(report, company_name="Empresa Test")
    xlsx = LibroDiarioExporter.to_excel(report, company_name="Empresa Test")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100
    _assert_excel_bytes(xlsx)


def test_libro_auxiliar_exporters_generate_valid_outputs() -> None:
    report = {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "cuentas": [
            {
                "cuenta": "1105",
                "nombre": "Caja",
                "total_debito": 1000,
                "total_credito": 300,
                "saldo": 700,
                "movimientos": [
                    {
                        "fecha": "2026-01-05",
                        "descripcion": "Ingreso caja",
                        "debito": 1000,
                        "credito": 0,
                    },
                    {
                        "fecha": "2026-01-07",
                        "descripcion": "Salida caja",
                        "debito": 0,
                        "credito": 300,
                    },
                ],
            }
        ],
    }

    pdf = LibroAuxiliarExporter.to_pdf(report, company_name="Empresa Test")
    xlsx = LibroAuxiliarExporter.to_excel(report, company_name="Empresa Test")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100
    _assert_excel_bytes(xlsx)


def test_cambios_patrimonio_exporters_generate_valid_outputs() -> None:
    report = {
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "cambios": [
            {
                "codigo": "31",
                "nombre": "Capital social",
                "movimiento_debito": 0,
                "movimiento_credito": 5000,
                "saldo_final": 5000,
            }
        ],
    }

    pdf = CambiosPatrimonioExporter.to_pdf(report, company_name="Empresa Test")
    xlsx = CambiosPatrimonioExporter.to_excel(report, company_name="Empresa Test")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100
    _assert_excel_bytes(xlsx)


def test_notas_estados_financieros_exporters_generate_valid_outputs() -> None:
    report = {
        "period_end": "2026-01-31",
        "resumen_financiero": {
            "activos": 100000,
            "pasivos": 40000,
            "patrimonio": 60000,
        },
        "notas": [
            {
                "numero": 1,
                "titulo": "Politicas contables",
                "contenido": "Resumen de criterios de reconocimiento.",
            }
        ],
    }

    pdf = NotasEstadosFinancierosExporter.to_pdf(report, company_name="Empresa Test")
    xlsx = NotasEstadosFinancierosExporter.to_excel(report, company_name="Empresa Test")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 100
    _assert_excel_bytes(xlsx)
