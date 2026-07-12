"""Unit tests for the DIAN form catalog, formula engine and PDF facsimile."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.dian_forms import get_catalog, has_catalog
from app.services.dian_forms.pdf_renderer import render_declaration
from app.services.tax_declaration_service import (
    _build_f300,
    build_draft_from_catalog,
)


class TestCatalog:
    def test_all_forms_registered(self):
        for ft in ["F300", "F350", "F110", "ICA"]:
            assert has_catalog(ft)
            assert get_catalog(ft).casillas

    def test_no_duplicate_casilla_numbers(self):
        for ft in ["F300", "F350", "F110", "ICA"]:
            nums = get_catalog(ft).numeros()
            assert len(nums) == len(set(nums)), f"{ft} has duplicate casillas"

    def test_f300_casilla_range(self):
        nums = set(get_catalog("F300").numeros())
        # Official F300 income/liquidation casillas.
        assert {"27", "41", "67", "81", "82", "88", "93"} <= nums

    def test_f110_casilla_range(self):
        nums = set(get_catalog("F110").numeros())
        assert {"44", "58", "72", "79", "99", "113", "114"} <= nums

    def test_f350_has_no_reteica(self):
        labels = " ".join(c.label for c in get_catalog("F350").casillas)
        assert "ReteICA" not in labels and "reteica" not in labels.lower()

    def test_f350_matrix_size(self):
        # 29-138 official casillas.
        assert len(get_catalog("F350").numeros()) == 110


class TestFormulaEngine:
    def test_f300_subtotals_chain(self):
        c = get_catalog("F300")
        values = {"28": 1_000_000.0, "59": 190_000.0, "72": 50_000.0}
        out = {}
        for cas in c.casillas:
            if cas.tipo == "subtotal" and cas.formula:
                out[cas.numero] = round(
                    cas.formula(lambda n: {**values, **out}.get(n, 0.0)), 2
                )
            else:
                out[cas.numero] = values.get(cas.numero, 0.0)
        assert out["41"] == 1_000_000.0  # total ingresos
        assert out["67"] == 190_000.0  # total IVA generado
        assert out["81"] == 50_000.0  # total descontable
        assert out["82"] == 140_000.0  # saldo a pagar = 67 - 81


class TestBuildDraftFromCatalog:
    def test_projects_all_official_casillas(self):
        settings = SimpleNamespace(iva_responsable=True)
        ledger = [
            {"account": "240805", "total_debit": 0.0, "total_credit": 190_000.0},
            {"account": "240802", "total_debit": 50_000.0, "total_credit": 0.0},
        ]
        computed, _ = _build_f300(
            ledger, settings, revenue_by_tipo={"gravado_19": 1_000_000.0}
        )
        fields = build_draft_from_catalog(get_catalog("F300"), computed)
        emitted = {f.renglon for f in fields}
        # Every catalog casilla is emitted (minus header rows; F300 has none).
        assert emitted == set(get_catalog("F300").numeros())
        # Each field carries a seccion.
        assert all(f.seccion for f in fields)

    def test_manual_casillas_flagged_for_review(self):
        settings = SimpleNamespace(iva_responsable=True)
        computed, _ = _build_f300([], settings, revenue_by_tipo={})
        fields = {
            f.renglon: f
            for f in build_draft_from_catalog(get_catalog("F300"), computed)
        }
        # Casilla 84 (saldo a favor anterior) is manual → requires review.
        assert fields["84"].requires_review is True
        assert fields["84"].source == "diligenciar_manual"


class TestPdfFacsimile:
    def test_render_returns_pdf_bytes(self):
        settings = SimpleNamespace(iva_responsable=True)
        computed, warns = _build_f300(
            [{"account": "240805", "total_debit": 0.0, "total_credit": 190_000.0}],
            settings,
            revenue_by_tipo={"gravado_19": 1_000_000.0},
        )
        fields = build_draft_from_catalog(get_catalog("F300"), computed)
        draft = {
            "form_type": "F300",
            "year": 2026,
            "period_start": "2026-01-01",
            "period_end": "2026-02-28",
            "company_nit": "901234567",
            "fields_json": [f.to_dict() for f in fields],
            "warnings_json": [w.to_dict() for w in warns],
        }
        pdf = render_declaration(draft, company_name="Empresa Demo SAS")
        assert pdf[:5] == b"%PDF-"
        assert len(pdf) > 1000
