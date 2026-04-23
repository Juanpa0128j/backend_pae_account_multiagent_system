"""
Exógena Service — Medios Magnéticos DIAN (2026)

Generates Formato 1001 (pagos a terceros y retenciones practicadas) and
Formato 2276 (ingresos recibidos por personas naturales) following strict
DIAN normalization rules:

  - NIT: digits only, no dots or dashes
  - Nombre: UPPERCASE, no accents, Ñ→N, special chars stripped
  - Ciudad: 5-digit DIAN municipality code
  - Amounts: integer pesos (no decimals in XML submission)

Source: Resolución DIAN 000162/2023 (Formato 1001 v10), Formato 2276 v7.

Usage:
    from app.services.exogena_service import generate_formato_1001

    rows = generate_formato_1001(db=db, company_nit="900123456", year=2025)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.models.database import CompanySettings

# ---------------------------------------------------------------------------
# Normalization helpers (DIAN strict format)
# ---------------------------------------------------------------------------

_NIT_RE = re.compile(r"[^0-9]")
_SPECIAL_RE = re.compile(r"[^A-Z0-9 \-\.]")


def normalize_nit_dian(nit: str) -> str:
    """Strip all non-digit characters from NIT (DIAN requires digits only)."""
    return _NIT_RE.sub("", nit or "")


def normalize_nombre_dian(nombre: str) -> str:
    """
    DIAN name normalization:
      1. Uppercase
      2. Ñ → N (before NFD decomposition which maps Ñ → N + combining tilde)
      3. NFD decompose → strip combining marks (removes accents)
      4. Strip remaining non-ASCII special characters
    """
    if not nombre:
        return ""
    nombre = nombre.upper()
    nombre = nombre.replace("Ñ", "N").replace("ñ", "N")
    nombre = unicodedata.normalize("NFD", nombre)
    nombre = "".join(c for c in nombre if unicodedata.category(c) != "Mn")
    nombre = _SPECIAL_RE.sub("", nombre)
    return nombre.strip()


def validate_and_normalize_tercero(
    nit: str,
    nombre: Optional[str],
    ciudad_codigo: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate and normalize a tercero record for DIAN exógena submission.

    Returns a dict with normalized values and a list of validation errors.
    An empty errors list means the record is submission-ready.
    """
    errors: List[str] = []
    nit_norm = normalize_nit_dian(nit)
    nombre_norm = normalize_nombre_dian(nombre or "")

    if not nit_norm:
        errors.append("NIT vacío o inválido")
    if not nombre_norm:
        errors.append("Nombre/razón social vacío")
    if ciudad_codigo and not re.match(r"^\d{5}$", str(ciudad_codigo)):
        errors.append(
            f"Código municipio DIAN inválido: {ciudad_codigo} (debe ser 5 dígitos)"
        )

    return {
        "nit": nit_norm,
        "nombre": nombre_norm,
        "ciudad_codigo": str(ciudad_codigo) if ciudad_codigo else None,
        "errors": errors,
        "submission_ready": len(errors) == 0,
    }


# ---------------------------------------------------------------------------
# Formato 1001 — Pagos o abonos en cuenta y retenciones practicadas
# ---------------------------------------------------------------------------

# DIAN concept codes for Formato 1001 (Resolución 000162/2023)
_CONCEPTO_SERVICIOS = "5001"
_CONCEPTO_COMPRAS = "5002"
_CONCEPTO_ARRENDAMIENTOS = "5003"
_CONCEPTO_OTROS = "5099"


def _classify_puc_concepto(cuenta_puc: str) -> str:
    """Map PUC account prefix to DIAN Formato 1001 concept code."""
    if cuenta_puc.startswith("51"):
        return _CONCEPTO_SERVICIOS
    if cuenta_puc.startswith("6"):
        return _CONCEPTO_COMPRAS
    if cuenta_puc.startswith("53"):
        return _CONCEPTO_ARRENDAMIENTOS
    return _CONCEPTO_OTROS


def generate_formato_1001(
    db: Session,
    company_nit: str,
    year: int,
) -> List[Dict[str, Any]]:
    """
    Generate Formato 1001 rows (pagos a terceros + retenciones practicadas).

    One row per (tercero_nit, concepto_dian) combination. Amounts are integer
    pesos as required by DIAN XML schema.

    Args:
        db: SQLAlchemy session
        company_nit: Reporting company NIT
        year: Tax year

    Returns:
        List of dicts ready for DIAN XML/CSV generation

    Raises:
        ValueError: if company not found
    """
    settings: Optional[CompanySettings] = (
        db.query(CompanySettings).filter(CompanySettings.nit == company_nit).first()
    )
    if not settings:
        raise ValueError(f"CompanySettings not found for NIT: {company_nit}")

    query = sql_text("""
        SELECT
            j.tercero_nit,
            COALESCE(t.razon_social, NULL) AS tercero_nombre,
            j.cuenta_puc,
            COALESCE(SUM(CASE WHEN j.cuenta_puc ~ '^[56]' THEN j.debito ELSE 0 END), 0) AS total_pagos,
            COALESCE(SUM(CASE WHEN j.cuenta_puc = '2365' THEN j.credito ELSE 0 END), 0) AS total_retefuente
        FROM journal_entry_lines j
        LEFT JOIN terceros t ON j.tercero_nit = t.nit
        WHERE j.company_nit = :company_nit
          AND EXTRACT(YEAR FROM j.fecha) = :year
          AND j.tercero_nit IS NOT NULL
          AND j.tercero_nit != ''
          AND (j.cuenta_puc ~ '^[56]' OR j.cuenta_puc = '2365')
        GROUP BY j.tercero_nit, t.razon_social, j.cuenta_puc
        HAVING
            COALESCE(SUM(CASE WHEN j.cuenta_puc ~ '^[56]' THEN j.debito ELSE 0 END), 0) > 0
            OR COALESCE(SUM(CASE WHEN j.cuenta_puc = '2365' THEN j.credito ELSE 0 END), 0) > 0
        ORDER BY j.tercero_nit, j.cuenta_puc
        """)
    rows = db.execute(query, {"company_nit": company_nit, "year": year}).fetchall()

    nit_norm_retenedor = normalize_nit_dian(company_nit)
    nombre_norm_retenedor = normalize_nombre_dian(settings.nombre or "")

    result: List[Dict[str, Any]] = []
    for row in rows:
        tercero_norm = validate_and_normalize_tercero(
            row.tercero_nit,
            row.tercero_nombre,
        )
        concepto = _classify_puc_concepto(row.cuenta_puc)
        result.append(
            {
                "formato": "1001",
                "year": year,
                "retenedor_nit": nit_norm_retenedor,
                "retenedor_nombre": nombre_norm_retenedor,
                "tercero_nit": tercero_norm["nit"],
                "tercero_nombre": tercero_norm["nombre"],
                "concepto_dian": concepto,
                "total_pagos": int(round(float(row.total_pagos))),
                "total_retefuente": int(round(float(row.total_retefuente))),
                "submission_ready": tercero_norm["submission_ready"],
                "validation_errors": tercero_norm["errors"],
            }
        )
    return result


# ---------------------------------------------------------------------------
# Formato 2276 — Ingresos recibidos por personas naturales/jurídicas
# ---------------------------------------------------------------------------


def generate_formato_2276(
    db: Session,
    company_nit: str,
    year: int,
) -> List[Dict[str, Any]]:
    """
    Generate Formato 2276 rows (ingresos recibidos).

    Aggregates class-4 credits per tercero (clientes) for the year.
    Reports income received from each natural/legal person.

    Args:
        db: SQLAlchemy session
        company_nit: Reporting company NIT
        year: Tax year

    Returns:
        List of dicts ready for DIAN XML/CSV generation

    Raises:
        ValueError: if company not found
    """
    settings: Optional[CompanySettings] = (
        db.query(CompanySettings).filter(CompanySettings.nit == company_nit).first()
    )
    if not settings:
        raise ValueError(f"CompanySettings not found for NIT: {company_nit}")

    query = sql_text("""
        SELECT
            j.tercero_nit,
            COALESCE(t.razon_social, NULL) AS tercero_nombre,
            COALESCE(SUM(j.credito), 0) AS total_ingresos
        FROM journal_entry_lines j
        LEFT JOIN terceros t ON j.tercero_nit = t.nit
        WHERE j.company_nit = :company_nit
          AND EXTRACT(YEAR FROM j.fecha) = :year
          AND j.cuenta_puc ~ '^4'
          AND j.tercero_nit IS NOT NULL
          AND j.tercero_nit != ''
        GROUP BY j.tercero_nit, t.razon_social
        HAVING COALESCE(SUM(j.credito), 0) > 0
        ORDER BY j.tercero_nit
        """)
    rows = db.execute(query, {"company_nit": company_nit, "year": year}).fetchall()

    nit_norm_receptor = normalize_nit_dian(company_nit)
    nombre_norm_receptor = normalize_nombre_dian(settings.nombre or "")

    result: List[Dict[str, Any]] = []
    for row in rows:
        tercero_norm = validate_and_normalize_tercero(
            row.tercero_nit,
            row.tercero_nombre,
        )
        result.append(
            {
                "formato": "2276",
                "year": year,
                "receptor_nit": nit_norm_receptor,
                "receptor_nombre": nombre_norm_receptor,
                "pagador_nit": tercero_norm["nit"],
                "pagador_nombre": tercero_norm["nombre"],
                "total_ingresos": int(round(float(row.total_ingresos))),
                "submission_ready": tercero_norm["submission_ready"],
                "validation_errors": tercero_norm["errors"],
            }
        )
    return result
