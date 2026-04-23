"""
Certificate Service — F220 Retención en la Fuente mass generation (2026)

Generates one F220 certificate per third-party (tercero) that received payments
from the company during the year. The F220 is the legal proof of withholding
delivered to providers/contractors so they can claim retenciones a favor in their
own income tax return (Art. 381 ET).

Responsibility for final signature and delivery rests with the Contador Público
(Ley 43/1990). All requires_review=True fields need explicit accountant action.

Usage:
    from app.services.certificate_service import generate_f220_certificates

    certs = generate_f220_certificates(db=db, company_nit="900123456", year=2025)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.models.database import CompanySettings

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class F220Certificate:
    """One F220 certificate per tercero per year."""

    company_nit: str
    company_nombre: str
    company_ciudad: str
    tercero_nit: str
    tercero_nombre: Optional[str]
    year: int
    total_pagos: float
    total_retefuente: float
    total_reteica: float
    conceptos: List[Dict[str, Any]] = field(default_factory=list)
    requires_review: bool = False
    review_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "retenedor": {
                "nit": self.company_nit,
                "nombre": self.company_nombre,
                "ciudad": self.company_ciudad,
            },
            "retenido": {
                "nit": self.tercero_nit,
                "nombre": self.tercero_nombre or "NOMBRE NO DISPONIBLE",
            },
            "year": self.year,
            "total_pagos": self.total_pagos,
            "total_retefuente": self.total_retefuente,
            "total_reteica": self.total_reteica,
            "conceptos": self.conceptos,
            "requires_review": self.requires_review,
            "review_reason": self.review_reason,
            "disclaimer": (
                "Certificado generado para revisión del Contador Público. "
                "La firma y entrega es responsabilidad del profesional habilitado (Ley 43/1990)."
            ),
        }


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------


def _get_payments_by_tercero(
    db: Session,
    company_nit: str,
    year: int,
) -> List[Dict[str, Any]]:
    """
    Aggregate total payments and retenciones per tercero for the year.
    Payments = sum of debits on class 5/6 accounts.
    Retefuente = sum of credits on account 2365.
    ReteICA = sum of credits on account 2368.
    """
    query = sql_text("""
        SELECT
            j.tercero_nit,
            COALESCE(t.razon_social, NULL) AS tercero_nombre,
            COALESCE(SUM(CASE WHEN j.cuenta_puc ~ '^[56]' THEN j.debito ELSE 0 END), 0) AS total_pagos,
            COALESCE(SUM(CASE WHEN j.cuenta_puc = '2365' THEN j.credito ELSE 0 END), 0) AS total_retefuente,
            COALESCE(SUM(CASE WHEN j.cuenta_puc = '2368' THEN j.credito ELSE 0 END), 0) AS total_reteica
        FROM journal_entry_lines j
        LEFT JOIN terceros t ON j.tercero_nit = t.nit
        WHERE j.company_nit = :company_nit
          AND EXTRACT(YEAR FROM j.fecha) = :year
          AND j.tercero_nit IS NOT NULL
          AND j.tercero_nit != ''
        GROUP BY j.tercero_nit, t.razon_social
        HAVING
            COALESCE(SUM(CASE WHEN j.cuenta_puc ~ '^[56]' THEN j.debito ELSE 0 END), 0) > 0
            OR COALESCE(SUM(CASE WHEN j.cuenta_puc = '2365' THEN j.credito ELSE 0 END), 0) > 0
        ORDER BY j.tercero_nit
        """)
    rows = db.execute(query, {"company_nit": company_nit, "year": year}).fetchall()
    return [dict(row._mapping) for row in rows]


def _get_conceptos_by_tercero(
    db: Session,
    company_nit: str,
    tercero_nit: str,
    year: int,
) -> List[Dict[str, Any]]:
    """Monthly breakdown of payments per tercero for certificate detail."""
    query = sql_text("""
        SELECT
            TO_CHAR(j.fecha, 'YYYY-MM') AS mes,
            COALESCE(SUM(CASE WHEN j.cuenta_puc ~ '^[56]' THEN j.debito ELSE 0 END), 0) AS pagos,
            COALESCE(SUM(CASE WHEN j.cuenta_puc = '2365' THEN j.credito ELSE 0 END), 0) AS retefuente,
            COALESCE(SUM(CASE WHEN j.cuenta_puc = '2368' THEN j.credito ELSE 0 END), 0) AS reteica
        FROM journal_entry_lines j
        WHERE j.company_nit = :company_nit
          AND j.tercero_nit = :tercero_nit
          AND EXTRACT(YEAR FROM j.fecha) = :year
        GROUP BY mes
        ORDER BY mes
        """)
    rows = db.execute(
        query,
        {"company_nit": company_nit, "tercero_nit": tercero_nit, "year": year},
    ).fetchall()
    return [
        {
            "mes": row.mes,
            "pagos": float(row.pagos),
            "retefuente": float(row.retefuente),
            "reteica": float(row.reteica),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_f220_certificates(
    db: Session,
    company_nit: str,
    year: int,
) -> List[F220Certificate]:
    """
    Generate F220 retention certificates for all terceros.

    One certificate per tercero that received payments subject to Retefuente
    or ReteICA during the year. Terceros without a matching record in the
    terceros catalog are flagged requires_review=True.

    Args:
        db: SQLAlchemy session
        company_nit: Company NIT (retenedor)
        year: Tax year (e.g. 2025)

    Returns:
        List of F220Certificate, one per tercero with activity

    Raises:
        ValueError: if company not found
    """
    settings: Optional[CompanySettings] = (
        db.query(CompanySettings).filter(CompanySettings.nit == company_nit).first()
    )
    if not settings:
        raise ValueError(f"CompanySettings not found for NIT: {company_nit}")

    rows = _get_payments_by_tercero(db, company_nit, year)
    certs: List[F220Certificate] = []

    for row in rows:
        tercero_nit = str(row["tercero_nit"])
        needs_review = row["tercero_nombre"] is None
        review_reason = (
            "Tercero no encontrado en catálogo — verifique NIT y razón social antes de emitir."
            if needs_review
            else None
        )
        conceptos = _get_conceptos_by_tercero(db, company_nit, tercero_nit, year)

        certs.append(
            F220Certificate(
                company_nit=company_nit,
                company_nombre=settings.nombre or "NOMBRE NO CONFIGURADO",
                company_ciudad=settings.ciudad or "CIUDAD NO CONFIGURADA",
                tercero_nit=tercero_nit,
                tercero_nombre=row["tercero_nombre"],
                year=year,
                total_pagos=float(row["total_pagos"]),
                total_retefuente=float(row["total_retefuente"]),
                total_reteica=float(row["total_reteica"]),
                conceptos=conceptos,
                requires_review=needs_review,
                review_reason=review_reason,
            )
        )

    return certs
