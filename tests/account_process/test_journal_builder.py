"""Pure unit tests for JournalBuilder.

No DB, no mocks — just input → output assertions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.account_process.journal_builder import JournalBuilder


class TestBuildFromIngest:
    def test_build_from_ingest_with_iva_and_retenciones(self) -> None:
        fecha = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        entries = JournalBuilder.build_from_ingest(
            fecha=fecha,
            cuenta_puc="5135",
            puc_descripcion="Servicios",
            total=Decimal("1190000"),
            iva=Decimal("190000"),
            retefuente=Decimal("47600"),
            reteica=Decimal("3570"),
            nit="900123456",
            descripcion="Consultoria tributaria",
        )

        assert len(entries) == 5

        # Expense debit
        assert entries[0] == {
            "fecha": "2026-03-15T10:30:00+00:00",
            "cuenta": "5135",
            "descripcion": "Servicios",
            "tercero_nit": "900123456",
            "detalle": "Consultoria tributaria",
            "debito": "1000000",
            "credito": "0",
        }

        # IVA debit
        assert entries[1] == {
            "fecha": "2026-03-15T10:30:00+00:00",
            "cuenta": "240802",
            "descripcion": "IVA Descontable",
            "tercero_nit": "900123456",
            "detalle": "IVA por Consultoria tributaria",
            "debito": "190000",
            "credito": "0",
        }

        # Vendor payable credit
        assert entries[2] == {
            "fecha": "2026-03-15T10:30:00+00:00",
            "cuenta": "220505",
            "descripcion": "Proveedores Nacionales",
            "tercero_nit": "900123456",
            "detalle": "CxP Consultoria tributaria",
            "debito": "0",
            "credito": "1138830",
        }

        # Retefuente credit
        assert entries[3] == {
            "fecha": "2026-03-15T10:30:00+00:00",
            "cuenta": "2365",
            "descripcion": "Retencion en la Fuente por pagar",
            "tercero_nit": "900123456",
            "detalle": "Retefuente Consultoria tributaria",
            "debito": "0",
            "credito": "47600",
        }

        # ReteICA credit
        assert entries[4] == {
            "fecha": "2026-03-15T10:30:00+00:00",
            "cuenta": "2368",
            "descripcion": "Retencion ICA por pagar",
            "tercero_nit": "900123456",
            "detalle": "ReteICA Consultoria tributaria",
            "debito": "0",
            "credito": "3570",
        }

        total_debits = sum(Decimal(e["debito"]) for e in entries)
        total_credits = sum(Decimal(e["credito"]) for e in entries)
        assert total_debits == total_credits

    def test_build_from_ingest_with_zero_retenciones(self) -> None:
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        entries = JournalBuilder.build_from_ingest(
            fecha=fecha,
            cuenta_puc="5135",
            puc_descripcion="Servicios",
            total=Decimal("1190000"),
            iva=Decimal("190000"),
            retefuente=Decimal("0"),
            reteica=Decimal("0"),
            nit="900123456",
            descripcion="Consultoria tributaria",
        )

        assert len(entries) == 3

        assert entries[0]["cuenta"] == "5135"
        assert entries[0]["debito"] == "1000000"
        assert entries[0]["credito"] == "0"

        assert entries[1]["cuenta"] == "240802"
        assert entries[1]["debito"] == "190000"
        assert entries[1]["credito"] == "0"

        assert entries[2]["cuenta"] == "220505"
        assert entries[2]["debito"] == "0"
        assert entries[2]["credito"] == "1190000"

        total_debits = sum(Decimal(e["debito"]) for e in entries)
        total_credits = sum(Decimal(e["credito"]) for e in entries)
        assert total_debits == total_credits

    def test_build_from_ingest_uses_custom_cuenta_reteica(self) -> None:
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        entries = JournalBuilder.build_from_ingest(
            fecha=fecha,
            cuenta_puc="5135",
            puc_descripcion="Servicios",
            total=Decimal("1190000"),
            iva=Decimal("190000"),
            retefuente=Decimal("47600"),
            reteica=Decimal("3570"),
            nit="900123456",
            descripcion="Consultoria tributaria",
            cuenta_reteica="2367",
        )
        reteica_entry = next(e for e in entries if e["cuenta"] == "2367")
        assert reteica_entry["credito"] == "3570"

    def test_build_from_ingest_raises_on_unbalanced(self) -> None:
        """If iva exceeds total the journal is unbalanced → ValueError."""
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match=r"Unbalanced journal entries \(ingest\)"):
            JournalBuilder.build_from_ingest(
                fecha=fecha,
                cuenta_puc="5135",
                puc_descripcion="Servicios",
                total=Decimal("100000"),
                iva=Decimal("150000"),
                retefuente=Decimal("0"),
                reteica=Decimal("0"),
                nit="900123456",
                descripcion="Consultoria tributaria",
            )

    def test_build_from_ingest_retenciones_exceed_total(self) -> None:
        """If retenciones exceed total, raise ValueError before balance check."""
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="retenciones exceed total"):
            JournalBuilder.build_from_ingest(
                fecha=fecha,
                cuenta_puc="5135",
                puc_descripcion="Servicios",
                total=Decimal("100000"),
                iva=Decimal("0"),
                retefuente=Decimal("60000"),
                reteica=Decimal("60000"),
                nit="900123456",
                descripcion="Consultoria tributaria",
            )

    @pytest.mark.parametrize(
        "field_name,bad_value",
        [
            ("total", Decimal("-1")),
            ("iva", Decimal("-1")),
            ("retefuente", Decimal("-1")),
            ("reteica", Decimal("-1")),
        ],
    )
    def test_build_from_ingest_rejects_negative_values(
        self, field_name: str, bad_value: Decimal
    ) -> None:
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        kwargs = {
            "fecha": fecha,
            "cuenta_puc": "5135",
            "puc_descripcion": "Servicios",
            "total": Decimal("100000"),
            "iva": Decimal("0"),
            "retefuente": Decimal("0"),
            "reteica": Decimal("0"),
            "nit": "900123456",
            "descripcion": "Consultoria tributaria",
        }
        kwargs[field_name] = bad_value
        with pytest.raises(ValueError, match=f"{field_name} must be non-negative"):
            JournalBuilder.build_from_ingest(**kwargs)

    def test_build_from_ingest_skips_zero_lines(self) -> None:
        """Ingest path suppresses zero lines by design."""
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        entries = JournalBuilder.build_from_ingest(
            fecha=fecha,
            cuenta_puc="5135",
            puc_descripcion="Servicios",
            total=Decimal("0"),
            iva=Decimal("0"),
            retefuente=Decimal("0"),
            reteica=Decimal("0"),
            nit="900123456",
            descripcion="Consultoria tributaria",
        )
        assert entries == []


class TestBuildFromContador:
    def test_build_from_contador_mixed_debit_credit(self) -> None:
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        asientos = [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 1190000,
                "descripcion": "Gasto por consultoria",
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores nacionales",
                "tipo_movimiento": "credito",
                "valor": 1190000,
                "descripcion": "CxP proveedor",
            },
        ]
        entries = JournalBuilder.build_from_contador(
            fecha=fecha,
            asientos=asientos,
            nit="900123456",
            descripcion="Consultoria tributaria",
        )

        assert len(entries) == 2

        by_cuenta = {e["cuenta"]: e for e in entries}
        assert "5135" in by_cuenta
        assert "2205" in by_cuenta

        assert by_cuenta["5135"]["debito"] == "1190000"
        assert by_cuenta["5135"]["credito"] == "0"
        assert by_cuenta["5135"]["descripcion"] == "Servicios"
        assert by_cuenta["5135"]["detalle"] == "Gasto por consultoria"

        assert by_cuenta["2205"]["debito"] == "0"
        assert by_cuenta["2205"]["credito"] == "1190000"
        assert by_cuenta["2205"]["descripcion"] == "Proveedores nacionales"
        assert by_cuenta["2205"]["detalle"] == "CxP proveedor"

        total_debits = sum(Decimal(e["debito"]) for e in entries)
        total_credits = sum(Decimal(e["credito"]) for e in entries)
        assert total_debits == total_credits

    def test_build_from_contador_empty_asientos(self) -> None:
        """Empty asientos list should produce empty entries and be balanced (0 == 0)."""
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        entries = JournalBuilder.build_from_contador(
            fecha=fecha,
            asientos=[],
            nit="900123456",
            descripcion="Consultoria tributaria",
        )
        assert entries == []

    def test_build_from_contador_zero_values(self) -> None:
        """Zero-value asientos should still be recorded."""
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        asientos = [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 0,
                "descripcion": "Sin valor",
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores",
                "tipo_movimiento": "credito",
                "valor": 0,
                "descripcion": "Sin valor",
            },
        ]
        entries = JournalBuilder.build_from_contador(
            fecha=fecha,
            asientos=asientos,
            nit="900123456",
            descripcion="Consultoria tributaria",
        )

        assert len(entries) == 2
        assert entries[0]["debito"] == "0"
        assert entries[0]["credito"] == "0"
        assert entries[1]["debito"] == "0"
        assert entries[1]["credito"] == "0"

    def test_build_from_contador_raises_on_unbalanced(self) -> None:
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        asientos = [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 1000000,
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores",
                "tipo_movimiento": "credito",
                "valor": 900000,
            },
        ]
        with pytest.raises(
            ValueError, match=r"Unbalanced journal entries \(contador\)"
        ):
            JournalBuilder.build_from_contador(
                fecha=fecha,
                asientos=asientos,
                nit="900123456",
                descripcion="Consultoria tributaria",
            )

    def test_build_from_contador_uses_fallback_descripcion(self) -> None:
        """If asiento lacks descripcion/nombre_cuenta, fall back to passed descripcion."""
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        asientos = [
            {
                "cuenta_puc": "5135",
                "tipo_movimiento": "debito",
                "valor": 500000,
            },
            {
                "cuenta_puc": "2205",
                "tipo_movimiento": "credito",
                "valor": 500000,
            },
        ]
        entries = JournalBuilder.build_from_contador(
            fecha=fecha,
            asientos=asientos,
            nit="900123456",
            descripcion="Fallback descripcion",
        )

        assert entries[0]["descripcion"] == "Fallback descripcion"
        assert entries[0]["detalle"] == "Fallback descripcion"
        assert entries[1]["descripcion"] == "Fallback descripcion"
        assert entries[1]["detalle"] == "Fallback descripcion"

    def test_build_from_contador_rejects_negative_valor(self) -> None:
        fecha = datetime(2026, 3, 15, tzinfo=timezone.utc)
        asientos = [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": -100000,
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores",
                "tipo_movimiento": "credito",
                "valor": 100000,
            },
        ]
        with pytest.raises(ValueError, match="valor must be non-negative"):
            JournalBuilder.build_from_contador(
                fecha=fecha,
                asientos=asientos,
                nit="900123456",
                descripcion="Consultoria tributaria",
            )
