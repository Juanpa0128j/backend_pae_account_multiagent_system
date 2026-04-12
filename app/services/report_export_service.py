"""
Export financial reports to PDF and Excel formats.

Provides presentation templates for:
- Balance Sheet (Balance General)
- Profit & Loss (Estado de Resultados)
- Cash Flow (Flujo de Caja)
"""

import io
from datetime import datetime
from typing import Any, Dict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _format_currency(value: float) -> str:
    """Format a number as Colombian currency."""
    if value is None:
        return "$ 0"
    return f"$ {value:,.0f}"


def _get_cuenta_codigo(cuenta: Dict[str, Any]) -> str:
    """Return account code from normalized or legacy keys."""
    return str(cuenta.get("codigo") or cuenta.get("cuenta") or "N/A")


def _get_cuenta_nombre(cuenta: Dict[str, Any]) -> str:
    """Return account name from normalized or legacy keys."""
    return str(cuenta.get("nombre") or cuenta.get("descripcion") or "N/A")


def _build_table_2cols(data, total_row_color: str):
    table = Table(data, colWidths=[3.5 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4788")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor(total_row_color)),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -2),
                    [colors.white, colors.HexColor("#f9f9f9")],
                ),
            ]
        )
    )
    return table


def _build_table_3cols(data, total_row_color: str):
    table = Table(data, colWidths=[1.2 * inch, 2.3 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4788")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor(total_row_color)),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -2),
                    [colors.white, colors.HexColor("#f9f9f9")],
                ),
            ]
        )
    )
    return table


class BalanceSheetExporter:
    """Export Balance Sheet reports to PDF and Excel."""

    @staticmethod
    def to_pdf(report: Dict[str, Any], company_name: str = "Empresa") -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.HexColor("#1f4788"),
            spaceAfter=6,
            alignment=1,
        )
        subtitle_style = ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#555555"),
            spaceAfter=12,
            alignment=1,
        )
        section_style = ParagraphStyle(
            "Section",
            parent=styles["Heading2"],
            fontSize=11,
            textColor=colors.HexColor("#1f4788"),
            spaceAfter=6,
            spaceBefore=8,
        )

        story = []
        story.append(Paragraph("BALANCE GENERAL", title_style))
        story.append(Paragraph("Estado de Situacion Financiera", subtitle_style))
        story.append(
            Paragraph(
                f"<b>Empresa:</b> {company_name} | "
                f"<b>Periodo:</b> al {report.get('period_end', '--')} | "
                f"<b>Generado:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 0.2 * inch))

        activos = float(report.get("activos", 0))
        pasivos = float(report.get("pasivos", 0))
        patrimonio = float(report.get("patrimonio", 0))
        utilidad = float(report.get("utilidad_neta", 0))

        story.append(Paragraph("ACTIVOS (Clase 1)", section_style))
        activos_detalle = report.get("activos_detalle") or []
        if activos_detalle:
            assets_data = [["Cuenta PUC", "Descripcion", "Valor (COP)"]]
            for cuenta in activos_detalle:
                assets_data.append(
                    [
                        _get_cuenta_codigo(cuenta),
                        _get_cuenta_nombre(cuenta),
                        _format_currency(float(cuenta.get("saldo", 0))),
                    ]
                )
            assets_data.append(["", "TOTAL ACTIVOS", _format_currency(activos)])
            story.append(_build_table_3cols(assets_data, "#e8f0f7"))
        else:
            assets_data = [
                ["Concepto", "Valor (COP)"],
                ["Detalle no disponible", ""],
                ["TOTAL ACTIVOS", _format_currency(activos)],
            ]
            story.append(_build_table_2cols(assets_data, "#e8f0f7"))
        story.append(Spacer(1, 0.15 * inch))

        story.append(Paragraph("PASIVOS (Clase 2)", section_style))
        pasivos_detalle = report.get("pasivos_detalle") or []
        if pasivos_detalle:
            liab_data = [["Cuenta PUC", "Descripcion", "Valor (COP)"]]
            for cuenta in pasivos_detalle:
                liab_data.append(
                    [
                        _get_cuenta_codigo(cuenta),
                        _get_cuenta_nombre(cuenta),
                        _format_currency(float(cuenta.get("saldo", 0))),
                    ]
                )
            liab_data.append(["", "TOTAL PASIVOS", _format_currency(pasivos)])
            story.append(_build_table_3cols(liab_data, "#fff4e6"))
        else:
            liab_data = [
                ["Concepto", "Valor (COP)"],
                ["Detalle no disponible", ""],
                ["TOTAL PASIVOS", _format_currency(pasivos)],
            ]
            story.append(_build_table_2cols(liab_data, "#fff4e6"))
        story.append(Spacer(1, 0.15 * inch))

        story.append(Paragraph("PATRIMONIO (Clase 3)", section_style))
        patrimonio_detalle = report.get("patrimonio_detalle") or []
        if patrimonio_detalle:
            equity_data = [["Cuenta PUC", "Descripcion", "Valor (COP)"]]
            for cuenta in patrimonio_detalle:
                equity_data.append(
                    [
                        _get_cuenta_codigo(cuenta),
                        _get_cuenta_nombre(cuenta),
                        _format_currency(float(cuenta.get("saldo", 0))),
                    ]
                )
            equity_data.append(["", "UTILIDAD NETA", _format_currency(utilidad)])
            equity_data.append(
                ["", "TOTAL PATRIMONIO", _format_currency(patrimonio + utilidad)]
            )
            table = Table(equity_data, colWidths=[1.2 * inch, 2.3 * inch, 2 * inch])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4788")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 10),
                        ("BACKGROUND", (0, -2), (-1, -1), colors.HexColor("#e6f7e6")),
                        ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
                        ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -3),
                            [colors.white, colors.HexColor("#f9f9f9")],
                        ),
                    ]
                )
            )
            story.append(table)
        else:
            equity_data = [
                ["Concepto", "Valor (COP)"],
                ["Detalle no disponible", ""],
                ["Ganancias del Ejercicio", _format_currency(utilidad)],
                ["TOTAL PATRIMONIO", _format_currency(patrimonio + utilidad)],
            ]
            story.append(_build_table_2cols(equity_data, "#e6f7e6"))
        story.append(Spacer(1, 0.2 * inch))

        cuadre = report.get("cuadre", False)
        msg = report.get("mensaje_cuadre", "Balance not validated")
        color_cuadre = (
            colors.HexColor("#28a745") if cuadre else colors.HexColor("#dc3545")
        )
        story.append(
            Paragraph(
                f"<b>Validacion de Cuadre:</b> {msg}",
                ParagraphStyle(
                    "Balance", parent=styles["Normal"], textColor=color_cuadre
                ),
            )
        )

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def to_excel(report: Dict[str, Any], company_name: str = "Empresa") -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Balance"

        header_fill = PatternFill(
            start_color="1F4788", end_color="1F4788", fill_type="solid"
        )
        header_font = Font(bold=True, color="FFFFFF", size=11)

        ws["A1"] = "BALANCE GENERAL"
        ws["A1"].font = Font(bold=True, size=14, color="1F4788")
        ws.merge_cells("A1:C1")

        ws["A2"] = f"Empresa: {company_name}"
        ws["A3"] = f"Periodo al: {report.get('period_end', '--')}"
        ws["A4"] = f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        row = 6

        def write_section(title: str, items: list, total: float):
            nonlocal row
            ws[f"A{row}"] = title
            ws[f"A{row}"].font = header_font
            ws[f"A{row}"].fill = header_fill
            ws[f"B{row}"].fill = header_fill
            ws[f"C{row}"].fill = header_fill
            row += 1

            ws[f"A{row}"] = "Cuenta PUC"
            ws[f"B{row}"] = "Descripcion"
            ws[f"C{row}"] = "Valor"
            ws[f"A{row}"].font = Font(bold=True)
            ws[f"B{row}"].font = Font(bold=True)
            ws[f"C{row}"].font = Font(bold=True)
            row += 1

            for cuenta in items:
                ws[f"A{row}"] = _get_cuenta_codigo(cuenta)
                ws[f"B{row}"] = _get_cuenta_nombre(cuenta)
                ws[f"C{row}"] = float(cuenta.get("saldo", 0))
                ws[f"C{row}"].number_format = "#,##0.00"
                row += 1

            ws[f"B{row}"] = f"TOTAL {title}"
            ws[f"C{row}"] = total
            ws[f"B{row}"].font = Font(bold=True)
            ws[f"C{row}"].font = Font(bold=True)
            ws[f"C{row}"].number_format = "#,##0.00"
            row += 2

        write_section(
            "ACTIVOS",
            report.get("activos_detalle") or [],
            float(report.get("activos", 0)),
        )
        write_section(
            "PASIVOS",
            report.get("pasivos_detalle") or [],
            float(report.get("pasivos", 0)),
        )

        patrimonio_items = list(report.get("patrimonio_detalle") or [])
        patrimonio_items.append(
            {
                "codigo": "",
                "nombre": "UTILIDAD NETA",
                "saldo": float(report.get("utilidad_neta", 0)),
            }
        )
        write_section(
            "PATRIMONIO",
            patrimonio_items,
            float(report.get("patrimonio", 0)) + float(report.get("utilidad_neta", 0)),
        )

        ws.column_dimensions["A"].width = 15
        ws.column_dimensions["B"].width = 45
        ws.column_dimensions["C"].width = 20

        for c in ws["C"]:
            if isinstance(c.value, (int, float)):
                c.alignment = Alignment(horizontal="right")

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()


class PnLExporter:
    """Export Profit & Loss reports to PDF and Excel."""

    @staticmethod
    def to_pdf(report: Dict[str, Any], company_name: str = "Empresa") -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.HexColor("#1f4788"),
            alignment=1,
        )
        section_style = ParagraphStyle(
            "Section",
            parent=styles["Heading2"],
            fontSize=11,
            textColor=colors.HexColor("#1f4788"),
            spaceBefore=8,
            spaceAfter=6,
        )

        story = []
        story.append(Paragraph("ESTADO DE RESULTADOS", title_style))
        story.append(
            Paragraph(
                f"<b>Empresa:</b> {company_name} | "
                f"<b>Periodo:</b> {report.get('period_start', '--')} a {report.get('period_end', '--')}",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 0.2 * inch))

        ingresos_total = float(report.get("total_ingresos", 0))
        story.append(Paragraph("INGRESOS OPERACIONALES", section_style))

        ingresos_data = [["Concepto", "Valor (COP)", "% Ingresos"]]
        for cuenta in report.get("ingresos", []):
            saldo = float(cuenta.get("saldo", 0))
            pct = (saldo / ingresos_total * 100) if ingresos_total > 0 else 0
            ingresos_data.append(
                [
                    f"{_get_cuenta_codigo(cuenta)} - {_get_cuenta_nombre(cuenta)}",
                    _format_currency(saldo),
                    f"{pct:.1f}%",
                ]
            )
        total_ingresos_pct = "100.0%" if ingresos_total > 0 else "0.0%"
        ingresos_data.append(
            ["TOTAL INGRESOS", _format_currency(ingresos_total), total_ingresos_pct]
        )

        ingresos_table = Table(
            ingresos_data, colWidths=[3 * inch, 1.8 * inch, 1.2 * inch]
        )
        ingresos_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4788")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f0f7")),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ]
            )
        )
        story.append(ingresos_table)
        story.append(Spacer(1, 0.15 * inch))

        costo_total = float(report.get("total_costo_ventas", 0))
        story.append(Paragraph("COSTO DE VENTAS", section_style))

        costo_data = [["Concepto", "Valor (COP)"]]
        for cuenta in report.get("costo_ventas", []):
            costo_data.append(
                [
                    f"{_get_cuenta_codigo(cuenta)} - {_get_cuenta_nombre(cuenta)}",
                    _format_currency(float(cuenta.get("saldo", 0))),
                ]
            )
        costo_data.append(["TOTAL COSTO DE VENTAS", _format_currency(costo_total)])

        costo_table = Table(costo_data, colWidths=[4 * inch, 2 * inch])
        costo_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4788")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fff4e6")),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ]
            )
        )
        story.append(costo_table)
        story.append(Spacer(1, 0.15 * inch))

        utilidad_bruta = float(report.get("utilidad_bruta", 0))
        gastos_total = float(report.get("total_gastos", 0))
        utilidad_neta = float(report.get("utilidad_neta", 0))

        summary_data = [
            ["UTILIDAD BRUTA", _format_currency(utilidad_bruta)],
            ["GASTOS OPERACIONALES", _format_currency(gastos_total)],
            ["UTILIDAD NETA DEL EJERCICIO", _format_currency(utilidad_neta)],
        ]
        summary_table = Table(summary_data, colWidths=[4 * inch, 2 * inch])
        summary_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f0f7")),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e6f7e6")),
                    ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ]
            )
        )
        story.append(summary_table)

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def to_excel(report: Dict[str, Any], company_name: str = "Empresa") -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "P&L"

        header_fill = PatternFill(
            start_color="1F4788", end_color="1F4788", fill_type="solid"
        )
        header_font = Font(bold=True, color="FFFFFF", size=11)

        ws["A1"] = "ESTADO DE RESULTADOS"
        ws["A1"].font = Font(bold=True, size=14, color="1F4788")
        ws.merge_cells("A1:B1")

        ws["A2"] = f"Empresa: {company_name}"
        ws["A3"] = (
            f"Periodo: {report.get('period_start', '--')} - {report.get('period_end', '--')}"
        )

        row = 5
        ws[f"A{row}"] = "INGRESOS OPERACIONALES"
        ws[f"A{row}"].font = header_font
        ws[f"A{row}"].fill = header_fill
        ws[f"B{row}"].fill = header_fill
        row += 1

        for cuenta in report.get("ingresos", []):
            ws[f"A{row}"] = (
                f"{_get_cuenta_codigo(cuenta)} - {_get_cuenta_nombre(cuenta)}"
            )
            ws[f"B{row}"] = float(cuenta.get("saldo", 0))
            ws[f"B{row}"].number_format = "#,##0.00"
            row += 1

        ws[f"A{row}"] = "TOTAL INGRESOS"
        ws[f"B{row}"] = float(report.get("total_ingresos", 0))
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"B{row}"].font = Font(bold=True)
        ws[f"B{row}"].number_format = "#,##0.00"
        row += 2

        ws[f"A{row}"] = "GASTOS OPERACIONALES"
        ws[f"A{row}"].font = header_font
        ws[f"A{row}"].fill = header_fill
        ws[f"B{row}"].fill = header_fill
        row += 1

        for cuenta in report.get("gastos", []):
            ws[f"A{row}"] = (
                f"{_get_cuenta_codigo(cuenta)} - {_get_cuenta_nombre(cuenta)}"
            )
            ws[f"B{row}"] = float(cuenta.get("saldo", 0))
            ws[f"B{row}"].number_format = "#,##0.00"
            row += 1

        ws[f"A{row}"] = "TOTAL GASTOS"
        ws[f"B{row}"] = float(report.get("total_gastos", 0))
        ws[f"A{row}"].font = Font(bold=True)
        ws[f"B{row}"].font = Font(bold=True)
        ws[f"B{row}"].number_format = "#,##0.00"
        row += 2

        ws[f"A{row}"] = "UTILIDAD NETA"
        ws[f"B{row}"] = float(report.get("utilidad_neta", 0))
        ws[f"A{row}"].font = Font(bold=True, size=12)
        ws[f"B{row}"].font = Font(bold=True, size=12)
        ws[f"A{row}"].fill = PatternFill(
            start_color="E6F7E6", end_color="E6F7E6", fill_type="solid"
        )
        ws[f"B{row}"].fill = PatternFill(
            start_color="E6F7E6", end_color="E6F7E6", fill_type="solid"
        )
        ws[f"B{row}"].number_format = "#,##0.00"

        ws.column_dimensions["A"].width = 45
        ws.column_dimensions["B"].width = 20

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()


class CashFlowExporter:
    """Export Cash Flow reports to PDF and Excel."""

    @staticmethod
    def to_pdf(report: Dict[str, Any], company_name: str = "Empresa") -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=16,
            textColor=colors.HexColor("#1f4788"),
            alignment=1,
        )
        section_style = ParagraphStyle(
            "Section",
            parent=styles["Heading2"],
            fontSize=11,
            textColor=colors.HexColor("#1f4788"),
        )

        story = []
        story.append(Paragraph("FLUJO DE CAJA", title_style))
        story.append(
            Paragraph(
                f"<b>Empresa:</b> {company_name} | "
                f"<b>Periodo:</b> {report.get('period_start', '--')} a {report.get('period_end', '--')} | "
                f"<b>Metodo:</b> Directo",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 0.2 * inch))

        story.append(Paragraph("CUENTAS DE EFECTIVO Y BANCOS", section_style))
        data = [["Cuenta PUC", "Descripcion", "Saldo Neto (COP)"]]

        total_efectivo = 0.0
        for cuenta in report.get("cuentas_efectivo", []):
            saldo = float(cuenta.get("saldo", 0))
            total_efectivo += saldo
            data.append(
                [
                    _get_cuenta_codigo(cuenta),
                    _get_cuenta_nombre(cuenta),
                    _format_currency(saldo),
                ]
            )

        data.append(
            ["", "TOTAL EFECTIVO Y EQUIVALENTES", _format_currency(total_efectivo)]
        )

        table = Table(data, colWidths=[1.2 * inch, 2.8 * inch, 2 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4788")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f0f7")),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ]
            )
        )

        story.append(table)
        story.append(Spacer(1, 0.2 * inch))
        story.append(
            Paragraph(f"<b>Metodologia:</b> {report.get('nota', '')}", styles["Normal"])
        )

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def to_excel(report: Dict[str, Any], company_name: str = "Empresa") -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Flujo de Caja"

        header_fill = PatternFill(
            start_color="1F4788", end_color="1F4788", fill_type="solid"
        )
        header_font = Font(bold=True, color="FFFFFF", size=11)

        ws["A1"] = "FLUJO DE CAJA - METODO DIRECTO"
        ws["A1"].font = Font(bold=True, size=14, color="1F4788")
        ws.merge_cells("A1:C1")

        ws["A2"] = f"Empresa: {company_name}"
        ws["A3"] = (
            f"Periodo: {report.get('period_start', '--')} - {report.get('period_end', '--')}"
        )

        row = 5
        headers = ["Cuenta PUC", "Descripcion", "Saldo (COP)"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col)
            cell.value = header
            cell.font = header_font
            cell.fill = header_fill
        row += 1

        total_efectivo = 0.0
        for cuenta in report.get("cuentas_efectivo", []):
            saldo = float(cuenta.get("saldo", 0))
            total_efectivo += saldo

            ws[f"A{row}"] = _get_cuenta_codigo(cuenta)
            ws[f"B{row}"] = _get_cuenta_nombre(cuenta)
            ws[f"C{row}"] = saldo
            ws[f"C{row}"].number_format = "#,##0.00"
            row += 1

        ws[f"A{row}"] = "TOTAL"
        ws[f"C{row}"] = total_efectivo
        ws[f"A{row}"].font = Font(bold=True, size=12)
        ws[f"C{row}"].font = Font(bold=True, size=12)
        ws[f"A{row}"].fill = PatternFill(
            start_color="E8F0F7", end_color="E8F0F7", fill_type="solid"
        )
        ws[f"C{row}"].fill = PatternFill(
            start_color="E8F0F7", end_color="E8F0F7", fill_type="solid"
        )
        ws[f"C{row}"].number_format = "#,##0.00"

        ws.column_dimensions["A"].width = 15
        ws.column_dimensions["B"].width = 35
        ws.column_dimensions["C"].width = 20

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()
