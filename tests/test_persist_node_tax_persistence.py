"""Focused tests for tax persistence mapping in db_persist_node."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agents.persist_node import db_persist_node


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


@patch("app.agents.persist_node.db_service.update_process_job")
@patch("app.agents.persist_node.db_service.create_journal_entry_lines")
@patch("app.agents.persist_node.db_service.create_transaction_posted")
@patch("app.agents.persist_node.db_service.validate_puc_exists")
@patch("app.agents.persist_node.db_service.check_duplicates", return_value=[])
@patch("app.agents.persist_node.db_service.update_ingest_job")
@patch("app.agents.persist_node.db_service.get_ingest_job")
@patch("app.agents.persist_node.SessionLocal")
def test_db_persist_maps_tributario_taxes_to_posted_transaction(
    mock_session_local,
    mock_get_ingest_job,
    mock_update_ingest,
    mock_check_duplicates,
    mock_validate_puc,
    mock_create_posted,
    mock_create_journal,
    mock_update_process,
):
    """db_persist_node should persist retefuente/reteica/iva values computed by tributario."""
    _ = (
        mock_update_ingest,
        mock_check_duplicates,
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
