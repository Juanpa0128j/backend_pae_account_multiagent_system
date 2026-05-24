"""F300 prorrateo Art. 490 ET — discriminated by tipo_iva.

Each scenario builds a synthetic revenue-by-tipo dict and asserts the
factor + renglones the F300 builder emits.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.tax_constants import (
    TIPO_IVA_EXCLUIDO,
    TIPO_IVA_EXENTO,
    TIPO_IVA_EXPORTACION,
    TIPO_IVA_GRAVADO_5,
    TIPO_IVA_GRAVADO_19,
)
from app.services.tax_declaration_service import (
    _build_f300,
    _compute_prorrateo_factor,
)


def _settings(**over):
    s = MagicMock()
    s.iva_responsable = True
    s.tasa_iva_general = Decimal("0.19")
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _ledger(iva_gen_19=1_900_000.0, iva_gen_5=0.0, iva_desc=380_000.0, ingresos_4=0.0):
    rows = [
        {
            "account": "240805",
            "name": "IVA Gen 19",
            "total_debit": 0.0,
            "total_credit": iva_gen_19,
            "net_balance": -iva_gen_19,
        },
        {
            "account": "240807",
            "name": "IVA Gen 5",
            "total_debit": 0.0,
            "total_credit": iva_gen_5,
            "net_balance": -iva_gen_5,
        },
        {
            "account": "240802",
            "name": "IVA Desc",
            "total_debit": iva_desc,
            "total_credit": 0.0,
            "net_balance": iva_desc,
        },
    ]
    if ingresos_4:
        rows.append(
            {
                "account": "4135",
                "name": "Ingresos",
                "total_debit": 0.0,
                "total_credit": ingresos_4,
                "net_balance": -ingresos_4,
            }
        )
    return rows


def _fields_by_renglon(fields):
    return {f.renglon: f for f in fields}


# ─── _compute_prorrateo_factor unit tests ──────────────────────────────────


def test_prorrateo_all_gravado_19_returns_factor_1():
    f, totals, review = _compute_prorrateo_factor({TIPO_IVA_GRAVADO_19: 1_000_000})
    assert f == 1.0
    assert totals["gravado_19"] == 1_000_000.0
    assert review is False


def test_prorrateo_all_excluido_returns_factor_0():
    f, _, review = _compute_prorrateo_factor({TIPO_IVA_EXCLUIDO: 500_000})
    assert f == 0.0
    assert review is True


def test_prorrateo_mixed_60_gravado_40_excluido():
    f, _, review = _compute_prorrateo_factor(
        {TIPO_IVA_GRAVADO_19: 600_000, TIPO_IVA_EXCLUIDO: 400_000}
    )
    assert f == pytest.approx(0.6)
    assert review is True


def test_prorrateo_exportacion_counts_as_descontable_eligible():
    # Art. 481 ET: exportaciones preserve descontable.
    f, _, _ = _compute_prorrateo_factor(
        {TIPO_IVA_EXPORTACION: 700_000, TIPO_IVA_EXCLUIDO: 300_000}
    )
    assert f == pytest.approx(0.7)


def test_prorrateo_exento_counts_as_descontable_eligible():
    # Art. 477/478 ET: exentos preserve descontable.
    f, _, _ = _compute_prorrateo_factor(
        {TIPO_IVA_EXENTO: 800_000, TIPO_IVA_EXCLUIDO: 200_000}
    )
    assert f == pytest.approx(0.8)


def test_prorrateo_gravado_5_mixed_with_excluido():
    f, _, _ = _compute_prorrateo_factor(
        {TIPO_IVA_GRAVADO_5: 500_000, TIPO_IVA_EXCLUIDO: 500_000}
    )
    assert f == pytest.approx(0.5)


def test_prorrateo_empty_dict_returns_factor_1():
    f, _, review = _compute_prorrateo_factor({})
    assert f == 1.0
    assert review is False


def test_prorrateo_only_sin_clasificar_flags_review():
    f, _, review = _compute_prorrateo_factor({"sin_clasificar": 100_000})
    assert f == 1.0
    assert review is True


def test_prorrateo_partial_sin_clasificar_flags_review():
    f, _, review = _compute_prorrateo_factor(
        {TIPO_IVA_GRAVADO_19: 800_000, "sin_clasificar": 200_000}
    )
    assert f == pytest.approx(1.0)
    assert review is True


# ─── _build_f300 integration tests ─────────────────────────────────────────


def test_build_f300_full_descontable_when_factor_1():
    fields, _ = _build_f300(
        _ledger(),
        _settings(),
        revenue_by_tipo={TIPO_IVA_GRAVADO_19: 10_000_000},
    )
    f = _fields_by_renglon(fields)
    # 67 = 66 * 1.0
    assert f["67"].value == 380_000.0
    assert f["67"].requires_review is False
    assert f["26"].value == 10_000_000.0


def test_build_f300_excluido_zeroes_descontable():
    fields, warnings = _build_f300(
        _ledger(),
        _settings(),
        revenue_by_tipo={TIPO_IVA_EXCLUIDO: 1_000_000},
    )
    f = _fields_by_renglon(fields)
    assert f["67"].value == 0.0
    assert f["67"].requires_review is True
    assert f["29"].value == 1_000_000.0
    assert any(w.field == "67" for w in warnings)


def test_build_f300_mix_60_40_prorrates_descontable():
    fields, warnings = _build_f300(
        _ledger(iva_desc=100_000.0),
        _settings(),
        revenue_by_tipo={TIPO_IVA_GRAVADO_19: 600_000, TIPO_IVA_EXCLUIDO: 400_000},
    )
    f = _fields_by_renglon(fields)
    assert f["67"].value == pytest.approx(60_000.0)
    assert f["67"].requires_review is True
    assert any(w.field == "67" for w in warnings)


def test_build_f300_exportaciones_keep_full_descontable():
    fields, _ = _build_f300(
        _ledger(iva_desc=200_000.0),
        _settings(),
        revenue_by_tipo={TIPO_IVA_EXPORTACION: 1_000_000},
    )
    f = _fields_by_renglon(fields)
    assert f["67"].value == 200_000.0
    assert f["30"].value == 1_000_000.0


def test_build_f300_renglon_54_sums_26_to_30():
    fields, _ = _build_f300(
        _ledger(),
        _settings(),
        revenue_by_tipo={
            TIPO_IVA_GRAVADO_19: 100.0,
            TIPO_IVA_GRAVADO_5: 50.0,
            TIPO_IVA_EXENTO: 20.0,
            TIPO_IVA_EXCLUIDO: 10.0,
            TIPO_IVA_EXPORTACION: 5.0,
        },
    )
    f = _fields_by_renglon(fields)
    assert f["54"].value == pytest.approx(185.0)


def test_build_f300_renglon_43_uses_240807():
    fields, _ = _build_f300(
        _ledger(iva_gen_5=50_000.0),
        _settings(),
        revenue_by_tipo={TIPO_IVA_GRAVADO_5: 1_000_000},
    )
    f = _fields_by_renglon(fields)
    assert f["43"].value == 50_000.0


def test_build_f300_unclassified_revenue_emits_warning():
    fields, warnings = _build_f300(
        _ledger(),
        _settings(),
        revenue_by_tipo={"sin_clasificar": 5_000_000},
    )
    f = _fields_by_renglon(fields)
    # factor 1.0 fallback: no recorte de descontable.
    assert f["67"].value == 380_000.0
    assert any("sin clasificar" in w.message.lower() for w in warnings)


def test_build_f300_no_revenue_breakdown_legacy_path():
    # Sin breakdown explícito: builder no debe romper, factor=1.
    fields, _ = _build_f300(_ledger(), _settings(), revenue_by_tipo=None)
    f = _fields_by_renglon(fields)
    assert f["67"].value == 380_000.0
    assert f["54"].value == 0.0


def test_build_f300_no_iva_responsable_skips_prorrateo():
    fields, _ = _build_f300(
        _ledger(),
        _settings(iva_responsable=False),
        revenue_by_tipo={TIPO_IVA_EXCLUIDO: 1_000_000},
    )
    f = _fields_by_renglon(fields)
    # No responsable -> descontable bruto = prorateado (no recorte).
    assert f["67"].value == 380_000.0
    assert f["67"].requires_review is False


def test_build_f300_warns_when_ledger_has_revenue_but_no_classification():
    ledger = _ledger(ingresos_4=2_000_000.0)
    fields, warnings = _build_f300(ledger, _settings(), revenue_by_tipo={})
    assert any(w.field == "26" for w in warnings)


def test_build_f300_iva_neto_uses_19_plus_5():
    fields, _ = _build_f300(
        _ledger(iva_gen_19=100.0, iva_gen_5=50.0, iva_desc=30.0),
        _settings(),
        revenue_by_tipo={TIPO_IVA_GRAVADO_19: 1_000},
    )
    f = _fields_by_renglon(fields)
    # 89 = (100+50) - 30 = 120
    assert f["89"].value == pytest.approx(120.0)


def test_build_f300_factor_zero_when_only_excluido_no_review_on_unclassified():
    fields, warnings = _build_f300(
        _ledger(iva_desc=500_000.0),
        _settings(),
        revenue_by_tipo={TIPO_IVA_EXCLUIDO: 1_000_000},
    )
    f = _fields_by_renglon(fields)
    assert f["67"].value == 0.0
    # No "sin clasificar" warning porque sin_clasificar=0.
    sin_clasif = [w for w in warnings if "sin clasificar" in w.message.lower()]
    assert sin_clasif == []


def test_build_f300_emits_renglones_26_27_28_29_30():
    fields, _ = _build_f300(
        _ledger(),
        _settings(),
        revenue_by_tipo={TIPO_IVA_GRAVADO_19: 1.0},
    )
    renglones = {f.renglon for f in fields}
    assert {"26", "27", "28", "29", "30"} <= renglones


def test_build_f300_emits_renglones_42_43_54_66_67_89():
    fields, _ = _build_f300(
        _ledger(),
        _settings(),
        revenue_by_tipo={TIPO_IVA_GRAVADO_19: 1.0},
    )
    renglones = {f.renglon for f in fields}
    assert {"42", "43", "54", "66", "67", "89"} <= renglones
