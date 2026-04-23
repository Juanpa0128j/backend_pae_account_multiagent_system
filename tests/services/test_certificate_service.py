"""
Unit tests for certificate_service.

DB queries are mocked so no real DB is needed.
Tests verify F220 certificate generation logic, requires_review flagging,
and monthly concepto breakdown structure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.certificate_service import (
    F220Certificate,
    generate_f220_certificates,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    s = MagicMock()
    s.nit = "900123456"
    s.nombre = "EMPRESA DEMO SAS"
    s.ciudad = "Medellín"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _mock_db(settings):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = settings
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGenerateF220:
    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_returns_one_cert_per_tercero(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)

        mock_payments.return_value = [
            {
                "tercero_nit": "800111222",
                "tercero_nombre": "PROVEEDOR UNO SAS",
                "total_pagos": 5_000_000.0,
                "total_retefuente": 200_000.0,
                "total_reteica": 48_000.0,
            },
            {
                "tercero_nit": "900333444",
                "tercero_nombre": "PROVEEDOR DOS LTDA",
                "total_pagos": 3_000_000.0,
                "total_retefuente": 120_000.0,
                "total_reteica": 0.0,
            },
        ]
        mock_conceptos.return_value = {}

        certs = generate_f220_certificates(db, "900123456", 2025)

        assert len(certs) == 2
        assert all(isinstance(c, F220Certificate) for c in certs)

    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_cert_fields_populated(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)

        mock_payments.return_value = [
            {
                "tercero_nit": "800111222",
                "tercero_nombre": "PROVEEDOR UNO SAS",
                "total_pagos": 5_000_000.0,
                "total_retefuente": 200_000.0,
                "total_reteica": 48_000.0,
            }
        ]
        mock_conceptos.return_value = {
            "800111222": [
                {
                    "mes": "2025-01",
                    "pagos": 5_000_000.0,
                    "retefuente": 200_000.0,
                    "reteica": 48_000.0,
                }
            ]
        }

        certs = generate_f220_certificates(db, "900123456", 2025)
        cert = certs[0]

        assert cert.company_nit == "900123456"
        assert cert.company_nombre == "EMPRESA DEMO SAS"
        assert cert.tercero_nit == "800111222"
        assert cert.tercero_nombre == "PROVEEDOR UNO SAS"
        assert cert.year == 2025
        assert cert.total_pagos == pytest.approx(5_000_000.0)
        assert cert.total_retefuente == pytest.approx(200_000.0)
        assert cert.total_reteica == pytest.approx(48_000.0)

    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_unknown_tercero_requires_review(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)

        mock_payments.return_value = [
            {
                "tercero_nit": "111222333",
                "tercero_nombre": None,
                "total_pagos": 1_000_000.0,
                "total_retefuente": 40_000.0,
                "total_reteica": 0.0,
            }
        ]
        mock_conceptos.return_value = {}

        certs = generate_f220_certificates(db, "900123456", 2025)
        cert = certs[0]

        assert cert.requires_review is True
        assert cert.review_reason is not None
        assert cert.tercero_nombre is None

    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_known_tercero_no_review_flag(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)

        mock_payments.return_value = [
            {
                "tercero_nit": "800111222",
                "tercero_nombre": "PROVEEDOR CONOCIDO SAS",
                "total_pagos": 2_000_000.0,
                "total_retefuente": 80_000.0,
                "total_reteica": 0.0,
            }
        ]
        mock_conceptos.return_value = {}

        certs = generate_f220_certificates(db, "900123456", 2025)
        assert certs[0].requires_review is False
        assert certs[0].review_reason is None

    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_to_dict_includes_disclaimer(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)

        mock_payments.return_value = [
            {
                "tercero_nit": "800111222",
                "tercero_nombre": "PROVEEDOR SAS",
                "total_pagos": 1_000_000.0,
                "total_retefuente": 40_000.0,
                "total_reteica": 0.0,
            }
        ]
        mock_conceptos.return_value = {}

        certs = generate_f220_certificates(db, "900123456", 2025)
        d = certs[0].to_dict()

        assert "disclaimer" in d
        assert "Ley 43/1990" in d["disclaimer"]

    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_conceptos_attached(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)

        mock_payments.return_value = [
            {
                "tercero_nit": "800111222",
                "tercero_nombre": "PROVEEDOR SAS",
                "total_pagos": 1_000_000.0,
                "total_retefuente": 40_000.0,
                "total_reteica": 0.0,
            }
        ]
        mock_conceptos.return_value = {
            "800111222": [
                {
                    "mes": "2025-03",
                    "pagos": 500_000.0,
                    "retefuente": 20_000.0,
                    "reteica": 0.0,
                },
                {
                    "mes": "2025-04",
                    "pagos": 500_000.0,
                    "retefuente": 20_000.0,
                    "reteica": 0.0,
                },
            ]
        }

        certs = generate_f220_certificates(db, "900123456", 2025)
        assert len(certs[0].conceptos) == 2
        assert certs[0].conceptos[0]["mes"] == "2025-03"

    def test_missing_company_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="CompanySettings not found"):
            generate_f220_certificates(db, "000000000", 2025)

    @patch("app.services.certificate_service._get_all_conceptos")
    @patch("app.services.certificate_service._get_payments_by_tercero")
    def test_empty_when_no_payments(self, mock_payments, mock_conceptos):
        settings = _make_settings()
        db = _mock_db(settings)
        mock_payments.return_value = []

        certs = generate_f220_certificates(db, "900123456", 2025)
        assert certs == []
