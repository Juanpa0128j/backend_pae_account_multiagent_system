from decimal import Decimal

from app.models.llm_schemas import AuditorOutputGemini, ContadorOutputGemini


def test_contador_output_backfills_missing_totals_from_asientos():
    payload = {
        "fecha_registro": "2026-03-24",
        "tipo_documento": "factura",
        "descripcion_general": "Prueba",
        "asientos": [
            {
                "cuenta_puc": "130505",
                "descripcion": "Clientes",
                "tipo_movimiento": "debito",
                "valor": "100000",
            },
            {
                "cuenta_puc": "4170",
                "descripcion": "Servicios",
                "tipo_movimiento": "credito",
                "valor": "100000",
            },
        ],
        # Simulate missing totals from LLM output
        "total_debitos": None,
        "total_creditos": None,
    }

    out = ContadorOutputGemini.model_validate(payload)
    assert out.total_debitos == Decimal("100000")
    assert out.total_creditos == Decimal("100000")


def test_auditor_output_tolerates_partial_hallazgo_payload():
    payload = {
        "fecha_auditoria": "2026-03-24",
        "documento_referencia": "FAC-001",
        "aprobado": False,
        "nivel_riesgo": "alto",
        "hallazgos": [
            {
                "codigo": "AUD-001",
                "severidad": "error",
                "descripcion": "Fecha inconsistente",
                # recomendacion intentionally missing
            }
        ],
        "puntaje_calidad": 35,
        "resumen": "Se detectaron hallazgos.",
    }

    out = AuditorOutputGemini.model_validate(payload)
    assert len(out.hallazgos) == 1
    assert out.hallazgos[0].recomendacion == ""
