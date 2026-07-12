"""Facsímil PDF de una declaración DIAN (borrador).

Renders a persisted declaration draft as a faithful facsimile of the official
form: header with taxpayer + period, sections in official order, one row per
casilla (número · etiqueta · valor), subtotals in bold, manual boxes flagged.
It is a working paper / borrador — NOT a filing document (the DIAN accepts only
MUISCA data entry / prevalidador XML). Reuses the ReportLab conventions from
``report_export_service``.
"""

from __future__ import annotations

import io
from html import escape
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_INK = colors.HexColor("#1f4788")
_MUTED = colors.HexColor("#555555")
_WARN = colors.HexColor("#B45309")
_SUBTOTAL_BG = colors.HexColor("#eef2fb")
_ZEBRA = colors.HexColor("#f9f9f9")

_TITLE = ParagraphStyle(
    "dianTitle", fontName="Helvetica-Bold", fontSize=15, textColor=_INK, spaceAfter=2
)
_SUBTITLE = ParagraphStyle(
    "dianSubtitle", fontName="Helvetica", fontSize=9.5, textColor=_MUTED, spaceAfter=10
)
_META = ParagraphStyle("dianMeta", fontName="Helvetica", fontSize=9, textColor=_MUTED)
_SECTION = ParagraphStyle(
    "dianSection",
    fontName="Helvetica-Bold",
    fontSize=11,
    textColor=_INK,
    spaceBefore=12,
    spaceAfter=4,
)
_CELL = ParagraphStyle("dianCell", fontName="Helvetica", fontSize=8.5, leading=10.5)
_CELL_B = ParagraphStyle(
    "dianCellB", fontName="Helvetica-Bold", fontSize=8.5, leading=10.5
)
_CELL_WARN = ParagraphStyle(
    "dianCellWarn", fontName="Helvetica", fontSize=8.5, leading=10.5, textColor=_WARN
)
_DISCLAIMER = ParagraphStyle(
    "dianDisc", fontName="Helvetica-Oblique", fontSize=7.5, textColor=_MUTED, leading=10
)


def _fmt(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return escape(str(value))
    neg = num < 0
    s = f"${abs(num):,.0f}".replace(",", ".")
    return f"-{s}" if neg else s


def _para(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def render_declaration(
    draft: Dict[str, Any] | Any, company_name: str = "Empresa"
) -> bytes:
    """Return a facsimile PDF (bytes) for a declaration draft.

    ``draft`` may be a dict or an ORM object exposing: form_type, year,
    period_start, period_end, company_nit, fields_json (or fields), warnings_json.
    """

    def _get(key: str, default: Any = None) -> Any:
        if isinstance(draft, dict):
            return draft.get(key, default)
        return getattr(draft, key, default)

    form_type = _get("form_type", "")
    year = _get("year", "")
    period_start = _get("period_start", "")
    period_end = _get("period_end", "")
    company_nit = _get("company_nit", "")
    fields: List[Dict[str, Any]] = list(
        _get("fields_json", None) or _get("fields", []) or []
    )
    warnings: List[Dict[str, Any]] = list(
        _get("warnings_json", None) or _get("warnings", []) or []
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        title=f"{form_type} {year} — Borrador",
    )
    story: List[Any] = []

    story.append(_para(f"Formulario {form_type} — Borrador", _TITLE))
    story.append(
        _para(f"Año gravable {year} · Período {period_start} → {period_end}", _SUBTITLE)
    )
    story.append(_para(f"Contribuyente: {company_name}", _META))
    story.append(_para(f"NIT: {company_nit}", _META))
    story.append(Spacer(1, 4))
    story.append(
        _para(
            "BORRADOR — no constituye presentación ante la DIAN. Pre-liquidación "
            "para revisión del Contador Público (Ley 43/1990).",
            _CELL_WARN,
        )
    )

    # Group by section, preserving first-seen order; skip the legal disclaimer row.
    order: List[str] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for f in fields:
        if f.get("renglon") == "_disclaimer":
            continue
        sec = f.get("seccion") or "General"
        if sec not in grouped:
            grouped[sec] = []
            order.append(sec)
        grouped[sec].append(f)

    for sec in order:
        story.append(_para(sec, _SECTION))
        rows = [
            [
                _para("Cas.", _CELL_B),
                _para("Concepto", _CELL_B),
                _para("Valor", _CELL_B),
            ]
        ]
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), _INK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d0d0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for f in grouped[sec]:
            idx = len(rows)
            is_sub = bool(f.get("es_subtotal"))
            needs_review = bool(f.get("requires_review"))
            label_style = _CELL_B if is_sub else (_CELL_WARN if needs_review else _CELL)
            num = str(f.get("renglon", ""))
            label = f.get("label", "")
            if needs_review:
                label = f"{label}  ⚠"
            rows.append(
                [
                    _para(num, _CELL_B if is_sub else _CELL),
                    _para(label, label_style),
                    _para(_fmt(f.get("value", 0)), _CELL_B if is_sub else _CELL),
                ]
            )
            if is_sub:
                style_cmds.append(("BACKGROUND", (0, idx), (-1, idx), _SUBTOTAL_BG))
            elif idx % 2 == 0:
                style_cmds.append(("BACKGROUND", (0, idx), (-1, idx), _ZEBRA))
        table = Table(
            rows, colWidths=[0.6 * inch, 4.5 * inch, 1.6 * inch], repeatRows=1
        )
        table.setStyle(TableStyle(style_cmds))
        story.append(table)

    if warnings:
        story.append(_para("Advertencias del contador", _SECTION))
        for w in warnings:
            field = w.get("field", "")
            msg = w.get("message", "")
            story.append(_para(f"• Casilla {field}: {msg}", _CELL))

    story.append(Spacer(1, 12))
    story.append(
        _para(
            "Los valores provienen del libro mayor; las casillas marcadas ⚠ requieren "
            "que el Contador Público las diligencie o confirme antes de transcribir la "
            "declaración en el portal MUISCA de la DIAN.",
            _DISCLAIMER,
        )
    )

    doc.build(story)
    return buffer.getvalue()
