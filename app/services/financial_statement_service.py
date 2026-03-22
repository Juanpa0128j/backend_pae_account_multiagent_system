"""Internal services for financial statements query and derived statement generation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.database import SessionLocal
from app.models.database import IngestStatus
from app.services import db_service
from app.services.nit_utils import normalize_nit


_REQUIRED_DERIVATION_INPUTS = ("balance_general", "estado_resultados", "libro_auxiliar")
_DERIVED_TARGETS = ("flujo_de_caja", "cambios_patrimonio", "notas_estados_financieros")


class BusinessRuleError(RuntimeError):
    """Raised when business preconditions for derived statements are not met."""


def list_financial_statements(
    *,
    company_nit: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    statement_type: str | None = None,
    source_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Internal read API for financial statements filtered by company and period."""
    normalized_nit = normalize_nit(company_nit)

    db = SessionLocal()
    try:
        rows = db_service.get_financial_statements(
            db,
            company_nit=normalized_nit,
            statement_type=statement_type,
            period_start=period_start,
            period_end=period_end,
            source_mode=source_mode,
        )
        return [
            {
                "id": row.id,
                "ingest_id": row.ingest_id,
                "statement_type": row.statement_type,
                "period_start": row.period_start.isoformat() if row.period_start else None,
                "period_end": row.period_end.isoformat() if row.period_end else None,
                "entity_nit": row.entity_nit,
                "source_mode": row.source_mode,
                "data": row.data,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    finally:
        db.close()


def derive_financial_statements(
    *,
    company_nit: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Derive cashflow/equity changes/notes from BG+ER+LA for one company and period.

    Raises:
        BusinessRuleError: when required source statements are not available.
    """
    normalized_nit = normalize_nit(company_nit)

    db = SessionLocal()
    try:
        source_rows = {}
        for stmt_type in _REQUIRED_DERIVATION_INPUTS:
            rows = db_service.get_financial_statements(
                db,
                company_nit=normalized_nit,
                statement_type=stmt_type,
                period_start=period_start,
                period_end=period_end,
            )
            if not rows:
                raise BusinessRuleError(
                    "Missing required inputs for derivation: "
                    f"{stmt_type}. Required set: {', '.join(_REQUIRED_DERIVATION_INPUTS)}"
                )
            source_rows[stmt_type] = rows[0]

        bg = source_rows["balance_general"].data or {}
        er = source_rows["estado_resultados"].data or {}
        la = source_rows["libro_auxiliar"].data or {}

        total_activos = Decimal(str(bg.get("total_activos") or 0))
        total_pasivos = Decimal(str(bg.get("total_pasivos") or 0))
        total_patrimonio = Decimal(str(bg.get("total_patrimonio") or 0))
        utilidad_neta = Decimal(str(er.get("utilidad_neta") or 0))

        accounts = la.get("lines") or la.get("accounts") or []
        efectivo_fin = Decimal("0")
        if isinstance(accounts, list):
            for acc in accounts:
                codigo = str(acc.get("cuenta_puc") or acc.get("codigo") or "")
                if codigo.startswith("11"):
                    saldo = acc.get("saldo") or acc.get("saldo_neto") or acc.get("total") or 0
                    efectivo_fin += Decimal(str(saldo))

        flujo_data = {
            "tipo": "flujo_de_caja",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "metodo": "indirecto",
            "flujo_neto_operacion": float(utilidad_neta),
            "flujo_neto_inversion": 0.0,
            "flujo_neto_financiacion": 0.0,
            "aumento_disminucion_neto": float(utilidad_neta),
            "efectivo_inicio_periodo": 0.0,
            "efectivo_fin_periodo": float(efectivo_fin),
            "verificacion": True,
            "moneda": "COP",
            "informacion_adicional": {
                "derivation_basis": ["balance_general", "estado_resultados", "libro_auxiliar"],
                "rule_version": "v1",
            },
        }

        cambios_data = {
            "tipo": "cambios_patrimonio",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "componentes": [
                {
                    "concepto_patrimonio": "resultado_ejercicio",
                    "saldo_inicial": float(total_patrimonio - utilidad_neta),
                    "movimientos": [
                        {
                            "concepto": "utilidad_neta_del_periodo",
                            "valor": float(utilidad_neta),
                        }
                    ],
                    "saldo_final": float(total_patrimonio),
                }
            ],
            "total_patrimonio_inicio": float(total_patrimonio - utilidad_neta),
            "total_patrimonio_fin": float(total_patrimonio),
            "moneda": "COP",
            "informacion_adicional": {
                "activos": float(total_activos),
                "pasivos": float(total_pasivos),
                "rule_version": "v1",
            },
        }

        notas_data = {
            "tipo": "notas_estados_financieros",
            "entidad": {"nit": normalized_nit},
            "periodo_inicio": period_start.date().isoformat(),
            "periodo_fin": period_end.date().isoformat(),
            "base_presentacion": "NIIF_pymes",
            "moneda_funcional": "COP",
            "hipotesis_negocio_en_marcha": True,
            "notas": [
                {
                    "numero_nota": "1",
                    "titulo": "Base de preparación y trazabilidad",
                    "categoria": "politicas_contables",
                    "contenido_resumido": (
                        "Notas derivadas automáticamente desde balance general, estado de "
                        "resultados y libro auxiliar para el mismo periodo y empresa."
                    ),
                    "cifras_relevantes": [
                        {"concepto": "total_activos", "valor": float(total_activos)},
                        {"concepto": "total_pasivos", "valor": float(total_pasivos)},
                        {"concepto": "utilidad_neta", "valor": float(utilidad_neta)},
                    ],
                }
            ],
            "informacion_adicional": {
                "derivation_basis": ["balance_general", "estado_resultados", "libro_auxiliar"],
                "rule_version": "v1",
            },
        }

        ingest_job = db_service.create_ingest_job(
            db,
            file_name=f"derived_{normalized_nit}_{period_end.date().isoformat()}",
            file_path="internal://derived_financial_statements",
            commit=False,
        )
        ingest_job.status = IngestStatus.COMPLETED
        ingest_job.document_type = "derived_financial_statements"
        ingest_job.pathway = "work_with_existing"

        created_rows = {}
        for statement_type, payload in (
            ("flujo_de_caja", flujo_data),
            ("cambios_patrimonio", cambios_data),
            ("notas_estados_financieros", notas_data),
        ):
            created_rows[statement_type] = db_service.create_financial_statement(
                db,
                ingest_id=ingest_job.id,
                statement_type=statement_type,
                entity_nit=normalized_nit,
                period_start=period_start,
                period_end=period_end,
                source_mode="derived",
                data=payload,
                commit=False,
            )

        for target_type in _DERIVED_TARGETS:
            target = created_rows[target_type]
            for source_type in _REQUIRED_DERIVATION_INPUTS:
                source = source_rows[source_type]
                db_service.create_financial_statement_lineage(
                    db,
                    target_statement_id=target.id,
                    source_statement_id=source.id,
                    relation_type="input",
                    commit=False,
                )

        db.commit()

        return {
            "status": "derived",
            "company_nit": normalized_nit,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "created_statement_ids": {k: v.id for k, v in created_rows.items()},
            "source_statement_ids": {k: v.id for k, v in source_rows.items()},
            "derived_count": len(created_rows),
            "lineage_links": len(_DERIVED_TARGETS) * len(_REQUIRED_DERIVATION_INPUTS),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
