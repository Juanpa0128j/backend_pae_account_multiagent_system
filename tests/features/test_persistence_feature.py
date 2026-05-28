"""Focused tests for tax persistence mapping in db_persist_node."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.persist_node import (
    _build_preview,
    db_persist_node,
)
from app.services.document_mappers import build_structured_transactions
from app.models.audit import AuditFinding, AuditReport, AuditTarget, Severity


def _build_state() -> dict:
    return {
        "file_path": "",
        "raw_text": "",
        "interpreted_data": {},
        "result": {},
        "error": None,
        "validation_history": [],
        "current_agent": "auditor",
        "correction_feedback": None,
        "retry_count": 0,
        "ingest_id": "ing_001",
        "db_result": None,
        "mode": "process",
        "raw_transactions": [
            {
                "fecha": "2026-03-07",
                "nit_emisor": "900123456",
                "nit_receptor": "800999888",
                "total": 1500000.0,
                "descripcion": "Servicios profesionales marzo 2026",
                "items": [{"descripcion": "Servicio", "cantidad": 1, "valor": 1500000}],
            }
        ],
        "contador_output": {
            "fecha_registro": "2026-03-07",
            "descripcion_general": "Servicios profesionales marzo 2026",
            "asientos": [
                {
                    "cuenta_puc": "5110",
                    "nombre_cuenta": "Honorarios",
                    "tipo_movimiento": "debito",
                    "valor": 1500000,
                    "descripcion": "Servicios profesionales",
                },
                {
                    "cuenta_puc": "220505",
                    "nombre_cuenta": "Proveedores",
                    "tipo_movimiento": "credito",
                    "valor": 1500000,
                    "descripcion": "Cuenta por pagar",
                },
            ],
            "total_debitos": 1500000,
            "total_creditos": 1500000,
        },
        "tributario_output": {
            "impuestos": [
                {
                    "tipo_impuesto": "retefuente",
                    "valor_impuesto": "165000.00",
                },
                {
                    "tipo_impuesto": "reteica",
                    "valor_impuesto": "10350.00",
                },
                {
                    "tipo_impuesto": "IVA",
                    "valor_impuesto": "285000.00",
                },
            ],
            "referencias_legales": ["Art. 383 ET", "Art. 477 ET"],
        },
        "process_id": "proc_001",
        "pending_transaction_id": "pending_001",
        "current_stage": "auditor_complete",
        "agent_log": [],
        "auditor_output": {
            "aprobado": True,
            "nivel_riesgo": "bajo",
            "puntaje_calidad": 95,
            "hallazgos": [],
        },
        "audit_approved": True,
        "audit_rejection_reason": None,
        "audit_decision": "approved",
        "audit_feedback": None,
    }


def _build_ingest_state_missing_receptor() -> dict:
    state = _build_state()
    state["mode"] = "ingest"
    state["process_id"] = None
    state["pending_transaction_id"] = None
    state["current_agent"] = "ingesta"
    state["company_nit"] = "800999888"
    state["contador_output"] = {}
    state["tributario_output"] = {}
    state["raw_transactions"] = [
        {
            "fecha": "2026-03-07",
            "nit_emisor": "900123456",
            "nit_receptor": "",
            "total": 1500000.0,
            "descripcion": "Servicios profesionales marzo 2026",
            "items": [],
        }
    ]
    return state


@patch("app.agents.persist_node.db_service.update_process_job")
@patch("app.agents.persist_node.db_service.create_journal_entry_lines")
@patch("app.agents.persist_node.db_service.create_transaction_posted")
@patch("app.agents.persist_node.db_service.validate_puc_exists")
@patch("app.agents.persist_node.db_service.find_duplicate_posted", return_value=None)
@patch("app.agents.persist_node.db_service.check_duplicates", return_value=[])
@patch("app.agents.persist_node.db_service.update_ingest_job")
@patch("app.agents.persist_node.db_service.get_ingest_job")
@patch("app.agents.persist_node.SessionLocal")
@patch("app.agents.persist_node._auto_derive_statements")
def test_db_persist_maps_tributario_taxes_to_posted_transaction(
    mock_auto_derive,
    mock_session_local,
    mock_get_ingest_job,
    mock_update_ingest,
    mock_check_duplicates,
    mock_find_duplicate,
    mock_validate_puc,
    mock_create_posted,
    mock_create_journal,
    mock_update_process,
):
    """db_persist_node should persist retefuente/reteica/iva values computed by tributario."""
    _ = (
        mock_auto_derive,
        mock_update_ingest,
        mock_check_duplicates,
        mock_find_duplicate,
        mock_create_journal,
        mock_update_process,
    )

    mock_db = MagicMock()
    mock_session_local.return_value = mock_db

    # Existing ingest job for provided ingest_id
    mock_get_ingest_job.return_value = SimpleNamespace(id="ing_001")

    # Existing pending transaction in process mode
    mock_pending = SimpleNamespace(
        id="pending_001",
        fecha=datetime(2026, 3, 7, tzinfo=timezone.utc),
        total=Decimal("1500000.00"),
        nit_emisor="900123456",
        nit_receptor="800999888",
        descripcion="Servicios profesionales marzo 2026",
    )
    mock_db.query.return_value.filter.return_value.first.return_value = mock_pending

    mock_validate_puc.return_value = SimpleNamespace(codigo="5110", nombre="Honorarios")
    mock_create_posted.return_value = SimpleNamespace(id="posted_001")

    state = _build_state()
    out = db_persist_node(state)

    assert out.get("error") is None
    assert mock_create_posted.call_count == 1

    kwargs = mock_create_posted.call_args.kwargs
    assert kwargs["transaction_pending_id"] == "pending_001"
    assert kwargs["retefuente"] == Decimal("165000.00")
    assert kwargs["reteica"] == Decimal("10350.00")
    assert kwargs["iva"] == Decimal("285000.00")


@patch("app.agents.persist_node.db_service.create_transaction_pending")
@patch("app.agents.persist_node.db_service.check_duplicates", return_value=[])
@patch("app.agents.persist_node.db_service.update_ingest_job")
@patch("app.agents.persist_node.db_service.get_ingest_job")
@patch("app.agents.persist_node.SessionLocal")
def test_db_persist_falls_back_to_company_nit_when_nit_receptor_missing(
    mock_session_local,
    mock_get_ingest_job,
    mock_update_ingest,
    mock_check_duplicates,
    mock_create_pending,
):
    _ = (mock_update_ingest, mock_check_duplicates)

    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    mock_get_ingest_job.return_value = SimpleNamespace(id="ing_001")
    mock_create_pending.return_value = SimpleNamespace(id="pending_001")

    state = _build_ingest_state_missing_receptor()
    out = db_persist_node(state)

    assert out.get("error") is None
    assert mock_create_pending.call_count == 1
    kwargs = mock_create_pending.call_args.kwargs
    assert kwargs["nit_receptor"] == "800999888"


def testbuild_structured_transactions_extracto_bancario_uses_movements_as_rows():
    interpreted = {
        "titular": {"nit": "900111222"},
        "periodo_inicio": "2026-01-01",
        "periodo_fin": "2026-01-31",
        "movements": [
            {
                "fecha": "2026-01-10",
                "descripcion": "Pago proveedor",
                "referencia": "TRX123",
                "debito": "120000",
                "credito": "0",
                "saldo": "880000",
            },
            {
                "fecha": "2026-01-11",
                "descripcion": "Ingreso venta",
                "referencia": "TRX124",
                "debito": "0",
                "credito": "450000",
                "saldo": "1330000",
            },
        ],
    }

    rows = build_structured_transactions(interpreted, "extracto_bancario")

    assert len(rows) == 2
    assert rows[0]["total"] == "120000"
    assert rows[1]["total"] == "450000"
    assert "TRX123" in rows[0]["descripcion"]


def testbuild_structured_transactions_nomina_uses_total_neto_pagar():
    interpreted = {
        "empresa": {"nit": "900222333"},
        "periodo_inicio": "2026-02-01",
        "periodo_fin": "2026-02-28",
        "total_neto_pagar": "3450000",
        "empleados": [
            {"nombre": "Ana", "neto_pagar": "1200000"},
            {"nombre": "Luis", "neto_pagar": "2250000"},
        ],
    }

    rows = build_structured_transactions(interpreted, "nomina")

    assert len(rows) == 1
    assert rows[0]["total"] == "3450000"
    assert rows[0]["nit_emisor"] == "900222333"
    assert "Nomina" in rows[0]["concepto"]


def testbuild_structured_transactions_recibo_impuesto_uses_total_pagado_and_fecha_pago():
    interpreted = {
        "numero_recibo": "RPI-001",
        "fecha_pago": "2026-03-15",
        "tipo_impuesto": "IVA",
        "nit_declarante": "901000999",
        "periodo_gravable": "2026-01",
        "total_pagado": "780000",
    }

    rows = build_structured_transactions(interpreted, "recibo_pago_impuesto")

    assert len(rows) == 1
    assert rows[0]["fecha"] == "2026-03-15"
    assert rows[0]["total"] == "780000"
    assert "IVA" in rows[0]["concepto"]


def test_build_preview_is_doc_type_aware_when_concept_missing():
    preview = _build_preview(
        {
            "nit_emisor": "900111222",
            "total": "0",
            "fecha": "2026-01-31",
            "concepto": "",
            "items": [{"a": 1}, {"b": 2}],
        },
        "extracto_bancario",
    )

    assert preview["concepto"] == "Extracto bancario"
    assert preview["items_count"] == 2


@patch("app.agents.persist_node.db_service.update_process_job")
@patch("app.agents.persist_node.db_service.create_journal_entry_lines")
@patch("app.agents.persist_node.db_service.create_transaction_posted")
@patch("app.agents.persist_node.db_service.validate_puc_exists")
@patch("app.agents.persist_node.db_service.check_duplicates", return_value=[])
@patch("app.agents.persist_node.db_service.update_ingest_job")
@patch("app.agents.persist_node.db_service.get_ingest_job")
@patch("app.agents.persist_node.SessionLocal")
@patch("app.agents.persist_node._auto_derive_statements")
@patch("app.agents.auditors.pre_persist_auditor.run")
def test_db_persist_refuses_when_pre_persist_blocker_present(
    mock_pre_persist_run,
    mock_auto_derive,
    mock_session_local,
    mock_get_ingest_job,
    mock_update_ingest,
    mock_check_duplicates,
    mock_validate_puc,
    mock_create_posted,
    mock_create_journal,
    mock_update_process,
):
    _ = (
        mock_auto_derive,
        mock_update_ingest,
        mock_check_duplicates,
        mock_validate_puc,
        mock_create_journal,
    )

    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    mock_get_ingest_job.return_value = SimpleNamespace(id="ing_001")

    mock_pending = SimpleNamespace(
        id="pending_001",
        fecha=datetime(2026, 3, 7, tzinfo=timezone.utc),
        total=Decimal("1500000.00"),
        nit_emisor="900123456",
        nit_receptor="800999888",
        descripcion="Servicios profesionales marzo 2026",
    )
    mock_db.query.return_value.filter.return_value.first.return_value = mock_pending

    mock_pre_persist_run.return_value = AuditReport(
        target=AuditTarget.PRE_PERSIST,
        approved=False,
        findings=[
            AuditFinding(
                target=AuditTarget.PRE_PERSIST,
                rule_id="PREP-PARTIDA-DOBLE-MISMATCH",
                severity=Severity.BLOCKER,
                fixable=False,
                responsible_agent="persist",
                technical_message="Partida doble mismatch.",
                user_message_es="La transacción no está balanceada.",
                suggested_action_es="Corregir asientos antes de persistir.",
            )
        ],
        attempt=1,
        duration_ms=1.0,
    )

    out = db_persist_node(_build_state())

    assert out.get("current_agent") == "audit_review_terminal"
    assert out.get("giveup_record") is not None
    assert mock_create_posted.call_count == 0
