"""Unit tests for preflight_service.run_preflight."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.preflight_service import (
    SEVERITY_BLOCKER,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    run_preflight,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    s = MagicMock()
    s.nit = "900123456"
    s.ciudad = "Medellín"
    s.codigo_ciiu = "6201"
    s.regimen_tributario = "ordinario"
    s.actividad_economica = "general"
    s.iva_responsable = True
    s.tasa_iva_general = Decimal("0.19")
    s.tasa_reteica = Decimal("0.00690")
    s.aplica_reteica = True
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _mock_db(settings):
    """Build a MagicMock db whose CompanySettings query returns `settings`."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = settings
    db.query.return_value = q
    return db


def _ledger_non_empty():
    return [
        {
            "account": "4135",
            "name": "Ingresos",
            "total_debit": 0.0,
            "total_credit": 1000.0,
            "net_balance": -1000.0,
        }
    ]


def _ledger_empty():
    return []


_UNSET = object()


def _patch_helpers(
    *,
    ledger=None,
    uvt=Decimal("52374"),
    reteica_tarifa=Decimal("0.00690"),
    base_minima_year=2026,
    base_minima_rows=None,
    tarifa_renta_result=_UNSET,
    retenciones=Decimal("0"),
    f2516_reviewed=None,
    pending_hitl=0,
):
    """Compose patches for all db_service helpers used by run_preflight."""
    if ledger is None:
        ledger = _ledger_non_empty()
    if base_minima_rows is None:
        base_minima_rows = [
            {
                "concepto": "retefuente_servicios",
                "uvt_units": "4",
                "year": base_minima_year,
            }
        ]
    if tarifa_renta_result is _UNSET:
        tarifa_renta_result = {
            "tarifa_base": 0.35,
            "sobretasa": 0.0,
            "tarifa_efectiva": 0.35,
            "base_legal": "Art. 240 ET",
        }

    patches = [
        patch(
            "app.services.preflight_service.db_service.get_general_ledger",
            return_value=ledger,
        ),
        patch("app.services.preflight_service.db_service.get_uvt", return_value=uvt),
        patch(
            "app.services.preflight_service.db_service.get_reteica_tarifa",
            return_value=reteica_tarifa,
        ),
        patch(
            "app.services.preflight_service.db_service.list_tax_constants",
            return_value={"uvt": None, "base_minima": base_minima_rows},
        ),
        patch(
            "app.services.preflight_service.db_service.get_tarifa_renta",
            return_value=tarifa_renta_result,
        ),
        patch(
            "app.services.preflight_service.db_service.sum_retenciones_anio",
            return_value=retenciones,
        ),
        patch(
            "app.services.preflight_service.db_service.get_latest_f2516_reviewed",
            return_value=f2516_reviewed,
        ),
        patch(
            "app.services.preflight_service._count_pending_hitl",
            return_value=pending_hitl,
        ),
    ]
    return patches


def _activate(patches):
    for p in patches:
        p.start()


def _deactivate(patches):
    for p in patches:
        p.stop()


def _find(checks, code):
    return next((c for c in checks if c["code"] == code), None)


# ---------------------------------------------------------------------------
# Base checks
# ---------------------------------------------------------------------------


class TestCompanySettingsCheck:
    def test_passes_with_full_settings(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "COMPANY_SETTINGS_COMPLETE")
        assert c["passed"] is True
        assert c["severity"] == SEVERITY_BLOCKER

    def test_fails_with_missing_ciudad(self):
        db = _mock_db(_make_settings(ciudad=None))
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "COMPANY_SETTINGS_COMPLETE")
        assert c["passed"] is False
        assert "ciudad" in c["metadata"]["missing_fields"]
        assert c["cta_path"] == "/settings"

    def test_fails_with_no_settings(self):
        db = _mock_db(None)
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "COMPANY_SETTINGS_COMPLETE")
        assert c["passed"] is False
        # No form-specific checks should be added when settings missing.
        assert _find(r["checks"], "IVA_RESPONSABLE") is None


class TestLedgerCheck:
    def test_passes_when_ledger_has_rows(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(ledger=_ledger_non_empty())
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "LEDGER_NOT_EMPTY")["passed"] is True

    def test_fails_when_ledger_empty(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(ledger=_ledger_empty())
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "LEDGER_NOT_EMPTY")["passed"] is False


class TestUvtCheck:
    def test_passes_when_uvt_available(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(uvt=Decimal("52374"))
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "UVT_YEAR_AVAILABLE")["passed"] is True

    def test_fails_when_uvt_missing(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(uvt=None)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "UVT_YEAR_AVAILABLE")
        assert c["passed"] is False
        assert c["cta_path"] == "/settings/tax-constants?year=2026"


class TestPeriodFutureCheck:
    def test_passes_when_period_is_past(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2024, 1, 1), date(2024, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "PERIOD_NOT_FUTURE")["passed"] is True

    def test_warns_when_period_is_future(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2099, 1, 1), date(2099, 2, 28)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "PERIOD_NOT_FUTURE")
        assert c["passed"] is False
        assert c["severity"] == SEVERITY_WARNING


class TestPendingHitlCheck:
    def test_passes_when_no_pending(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(pending_hitl=0)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "NO_PENDING_HITL")["passed"] is True

    def test_warns_when_pending(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(pending_hitl=3)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "NO_PENDING_HITL")
        assert c["passed"] is False
        assert c["metadata"]["pending_count"] == 3


# ---------------------------------------------------------------------------
# F300 specific
# ---------------------------------------------------------------------------


class TestF300Checks:
    def test_iva_responsable_blocker_when_false(self):
        db = _mock_db(_make_settings(iva_responsable=False))
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "IVA_RESPONSABLE")["passed"] is False
        assert r["ready"] is False

    def test_tasa_iva_blocker_when_zero(self):
        db = _mock_db(_make_settings(tasa_iva_general=Decimal("0")))
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "TASA_IVA_GENERAL")["passed"] is False

    def test_reteica_skipped_when_not_applicable(self):
        db = _mock_db(_make_settings(aplica_reteica=False, tasa_reteica=Decimal("0")))
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "RETEICA_TARIFA_AVAILABLE") is None

    def test_reteica_blocker_when_no_tarifa(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(reteica_tarifa=None)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "RETEICA_TARIFA_AVAILABLE")["passed"] is False

    def test_iva_periodicidad_bimestral_matches(self):
        db = _mock_db(_make_settings())
        # prev-year ledger needs to be large; we mock get_general_ledger to return
        # huge gross income for "any" call → bimestral expected.
        big_ledger = [
            {
                "account": "4135",
                "name": "x",
                "total_debit": 0.0,
                "total_credit": 1e12,
                "net_balance": -1e12,
            }
        ]
        patches = _patch_helpers(ledger=big_ledger)
        _activate(patches)
        try:
            # 60-day period
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 3, 1)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "IVA_PERIODICIDAD_VALID")["passed"] is True

    def test_iva_periodicidad_mismatch_warns(self):
        db = _mock_db(_make_settings())
        # Tiny ledger → cuatrimestral expected. But user picks 60-day → mismatch.
        small = [
            {
                "account": "4135",
                "name": "x",
                "total_debit": 0.0,
                "total_credit": 10.0,
                "net_balance": -10.0,
            }
        ]
        patches = _patch_helpers(ledger=small)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2026, 1, 1), date(2026, 3, 1)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "IVA_PERIODICIDAD_VALID")
        assert c["passed"] is False
        assert c["metadata"]["expected"] == "cuatrimestral"


# ---------------------------------------------------------------------------
# F350 specific
# ---------------------------------------------------------------------------


class TestF350Checks:
    def test_base_minima_blocker_when_empty(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(base_minima_rows=[])
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F350", date(2026, 1, 1), date(2026, 1, 31)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "BASE_MINIMA_AVAILABLE")
        assert c["passed"] is False
        assert c["cta_path"] == "/settings/tax-constants?year=2026"

    def test_base_minima_passes_when_rows_exist(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F350", date(2026, 1, 1), date(2026, 1, 31)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "BASE_MINIMA_AVAILABLE")["passed"] is True


# ---------------------------------------------------------------------------
# F110 specific
# ---------------------------------------------------------------------------


class TestF110Checks:
    def test_f2516_passes_below_threshold(self):
        db = _mock_db(_make_settings())
        # ledger gives prev-year gross < threshold (tiny)
        small = [
            {
                "account": "4135",
                "name": "x",
                "total_debit": 0.0,
                "total_credit": 10.0,
                "net_balance": -10.0,
            }
        ]
        patches = _patch_helpers(ledger=small, f2516_reviewed=None)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "F2516_REVIEWED")
        assert c["passed"] is True
        assert c["metadata"]["required"] is False

    def test_f2516_blocker_above_threshold_missing(self):
        db = _mock_db(_make_settings())
        big = [
            {
                "account": "4135",
                "name": "x",
                "total_debit": 0.0,
                "total_credit": 1e12,
                "net_balance": -1e12,
            }
        ]
        patches = _patch_helpers(ledger=big, f2516_reviewed=None)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "F2516_REVIEWED")
        assert c["passed"] is False
        assert c["cta_path"] == "/tax?tab=declarations&form=F2516&year=2026"

    def test_f2516_passes_above_threshold_when_reviewed(self):
        db = _mock_db(_make_settings())
        big = [
            {
                "account": "4135",
                "name": "x",
                "total_debit": 0.0,
                "total_credit": 1e12,
                "net_balance": -1e12,
            }
        ]
        fake_f2516 = MagicMock()
        patches = _patch_helpers(ledger=big, f2516_reviewed=fake_f2516)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "F2516_REVIEWED")["passed"] is True

    def test_tarifas_renta_blocker_when_missing(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(tarifa_renta_result=None)
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "TARIFAS_RENTA_AVAILABLE")["passed"] is False

    def test_retenciones_info_when_positive(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(retenciones=Decimal("500000"))
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F110", date(2026, 1, 1), date(2026, 12, 31)
            )
        finally:
            _deactivate(patches)
        c = _find(r["checks"], "RETENCIONES_ANIO_ANTERIOR")
        assert c["severity"] == SEVERITY_INFO
        assert c["passed"] is True


# ---------------------------------------------------------------------------
# ICA / F2516 / invalid form_type
# ---------------------------------------------------------------------------


class TestICAChecks:
    def test_ica_includes_reteica(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers(reteica_tarifa=Decimal("0.00690"))
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "ICA", date(2026, 1, 1), date(2026, 1, 31)
            )
        finally:
            _deactivate(patches)
        assert _find(r["checks"], "RETEICA_TARIFA_AVAILABLE")["passed"] is True


class TestF2516NoExtraChecks:
    def test_f2516_only_base_checks(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F2516", date(2026, 1, 1), date(2026, 12, 31)
            )
        finally:
            _deactivate(patches)
        # No form-specific blockers beyond base
        assert _find(r["checks"], "IVA_RESPONSABLE") is None
        assert _find(r["checks"], "BASE_MINIMA_AVAILABLE") is None
        assert _find(r["checks"], "F2516_REVIEWED") is None


class TestInvalidFormType:
    def test_raises_value_error(self):
        db = _mock_db(_make_settings())
        with pytest.raises(ValueError):
            run_preflight(db, "900123456", "BOGUS", date(2026, 1, 1), date(2026, 2, 28))


class TestReadyAggregation:
    def test_ready_true_when_no_blockers_failed(self):
        db = _mock_db(_make_settings())
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F350", date(2024, 1, 1), date(2024, 1, 31)
            )
        finally:
            _deactivate(patches)
        assert r["ready"] is True
        assert r["blockers"] == 0

    def test_ready_false_when_blocker_fails(self):
        db = _mock_db(_make_settings(ciudad=None))
        patches = _patch_helpers()
        _activate(patches)
        try:
            r = run_preflight(
                db, "900123456", "F300", date(2024, 1, 1), date(2024, 2, 28)
            )
        finally:
            _deactivate(patches)
        assert r["ready"] is False
        assert r["blockers"] >= 1
