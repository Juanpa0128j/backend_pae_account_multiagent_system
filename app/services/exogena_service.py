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


# DIAN NIT verification-digit weights (right-to-left), Art. 555-1 ET.
_DV_WEIGHTS = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]


def nit_dv(nit: str) -> str:
    """Compute the DIAN check digit (dígito de verificación) for a NIT.

    Returns '' when the NIT is empty/invalid so callers can leave the column blank.
    """
    digits = normalize_nit_dian(nit)
    if not digits:
        return ""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        if i >= len(_DV_WEIGHTS):
            break
        total += int(ch) * _DV_WEIGHTS[i]
    resto = total % 11
    return str(resto if resto <= 1 else 11 - resto)


def _tercero_identity(nit: str, nombre: Optional[str]) -> Dict[str, Any]:
    """Official identity columns shared by 1007/1008/1009 (and 1001).

    We hold only NIT + razón social, so the apellidos/nombres split, dirección
    and municipio stay blank for the accountant to complete; tipo_documento is a
    length heuristic (31=NIT / 13=cédula) and país defaults to 169 (Colombia).
    """
    t = validate_and_normalize_tercero(nit, nombre)
    nit_norm = t["nit"]
    # Colombian NITs start with 8 or 9; otherwise treat as cédula (13). The
    # accountant confirms — flagged for review via _errors when ambiguous.
    is_nit = nit_norm[:1] in ("8", "9")
    return {
        "tipo_documento": "31" if is_nit else "13",
        "numero_identificacion": nit_norm,
        "dv": nit_dv(nit_norm) if is_nit else "",
        "primer_apellido": "",
        "segundo_apellido": "",
        "primer_nombre": "",
        "otros_nombres": "",
        "razon_social": t["nombre"],
        "direccion": "",
        "codigo_dpto": "",
        "codigo_mcp": "",
        "pais_residencia": "169",
        "_submission_ready": t["submission_ready"],
        "_errors": t["errors"],
    }


def _classify_1007(cuenta_puc: str) -> str:
    """Ingresos: 41 → 4001 (act. ordinarias), demás clase 4 → 4002 (otros)."""
    return "4001" if cuenta_puc.startswith("41") else "4002"


def _classify_1008(cuenta_puc: str) -> str:
    """CxC: 1305 → 1315 (clientes); demás clase 13 → 1317 (otras)."""
    return "1315" if cuenta_puc.startswith("1305") else "1317"


def _classify_1009(cuenta_puc: str) -> str:
    """CxP: 2205→2201 (proveedores), 21→2203 (financieras), 24→2204 (impuestos),
    demás → 2206."""
    if cuenta_puc.startswith("2205"):
        return "2201"
    if cuenta_puc.startswith("21"):
        return "2203"
    if cuenta_puc.startswith("24"):
        return "2204"
    return "2206"


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

    # Aggregate by (tercero_nit, concepto_dian) — multiple PUCs can map to the
    # same DIAN concept code, so we sum them into one row per concept.
    aggregated: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        concepto = _classify_puc_concepto(row.cuenta_puc)
        key = (row.tercero_nit, concepto)
        if key not in aggregated:
            tercero_norm = validate_and_normalize_tercero(
                row.tercero_nit,
                row.tercero_nombre,
            )
            aggregated[key] = {
                "formato": "1001",
                "year": year,
                "retenedor_nit": nit_norm_retenedor,
                "retenedor_nombre": nombre_norm_retenedor,
                "tercero_nit": tercero_norm["nit"],
                "tercero_nombre": tercero_norm["nombre"],
                "concepto_dian": concepto,
                "total_pagos": 0,
                "total_retefuente": 0,
                "listo_para_envio": "Sí" if tercero_norm["submission_ready"] else "No",
                "errores_validacion": tercero_norm["errors"],
            }
        aggregated[key]["total_pagos"] += int(round(float(row.total_pagos)))
        aggregated[key]["total_retefuente"] += int(round(float(row.total_retefuente)))

    return list(aggregated.values())


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
                "listo_para_envio": "Sí" if tercero_norm["submission_ready"] else "No",
                "errores_validacion": tercero_norm["errors"],
            }
        )
    return result


# ---------------------------------------------------------------------------
# Helpers shared by 1007/1008/1009
# ---------------------------------------------------------------------------


def _require_company(db: Session, company_nit: str) -> None:
    if not db.query(CompanySettings).filter(CompanySettings.nit == company_nit).first():
        raise ValueError(f"CompanySettings not found for NIT: {company_nit}")


# PUC prefix patterns interpolated into the ~ regex operator. Allowlisted so the
# f-string interpolation can never carry untrusted input into SQL.
_ALLOWED_PREFIX_REGEX = {"^4", "^13", "^2[123]"}


def _tercero_movimientos(
    db: Session, company_nit: str, year: int, prefix_regex: str, cumulative: bool
) -> List[Any]:
    """Sum debit/credit per (tercero, cuenta_puc) for the accounts matching
    ``prefix_regex``. ``cumulative`` True → balance up to 31-dic (saldos: <= year);
    False → movements of the year (flujos: = year)."""
    if prefix_regex not in _ALLOWED_PREFIX_REGEX:
        raise ValueError(f"prefix_regex no permitido: {prefix_regex!r}")
    year_filter = (
        "EXTRACT(YEAR FROM j.fecha) <= :year"
        if cumulative
        else "EXTRACT(YEAR FROM j.fecha) = :year"
    )
    query = sql_text(f"""
        SELECT
            j.tercero_nit,
            COALESCE(t.razon_social, NULL) AS tercero_nombre,
            j.cuenta_puc,
            COALESCE(SUM(j.debito), 0) AS total_debito,
            COALESCE(SUM(j.credito), 0) AS total_credito
        FROM journal_entry_lines j
        LEFT JOIN terceros t ON j.tercero_nit = t.nit
        WHERE j.company_nit = :company_nit
          AND {year_filter}
          AND j.cuenta_puc ~ '{prefix_regex}'
          AND j.tercero_nit IS NOT NULL
          AND j.tercero_nit != ''
        GROUP BY j.tercero_nit, t.razon_social, j.cuenta_puc
        ORDER BY j.tercero_nit, j.cuenta_puc
        """)
    return db.execute(query, {"company_nit": company_nit, "year": year}).fetchall()


# ---------------------------------------------------------------------------
# Formato 1007 — Ingresos recibidos
# ---------------------------------------------------------------------------


def generate_formato_1007(
    db: Session, company_nit: str, year: int
) -> List[Dict[str, Any]]:
    """Ingresos recibidos (clase 4) por tercero y concepto (4001/4002).

    Columns follow the official layout: concepto, identity, país, ingresos
    brutos and devoluciones/rebajas/descuentos.
    """
    _require_company(db, company_nit)
    rows = _tercero_movimientos(db, company_nit, year, "^4", cumulative=False)

    aggregated: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        concepto = _classify_1007(row.cuenta_puc)
        key = (row.tercero_nit, concepto)
        if key not in aggregated:
            ident = _tercero_identity(row.tercero_nit, row.tercero_nombre)
            aggregated[key] = {
                "formato": "1007",
                "concepto": concepto,
                **{k: v for k, v in ident.items() if not k.startswith("_")},
                "ingresos_brutos": 0,
                "devoluciones_rebajas_descuentos": 0,
                "listo_para_envio": "Sí" if ident["_submission_ready"] else "No",
                "errores_validacion": ident["_errors"],
            }
        aggregated[key]["ingresos_brutos"] += int(round(float(row.total_credito)))
        aggregated[key]["devoluciones_rebajas_descuentos"] += int(
            round(float(row.total_debito))
        )

    return [r for r in aggregated.values() if r["ingresos_brutos"] > 0]


# ---------------------------------------------------------------------------
# Formato 1008 — Saldo de cuentas por cobrar a 31-12
# ---------------------------------------------------------------------------


def generate_formato_1008(
    db: Session, company_nit: str, year: int
) -> List[Dict[str, Any]]:
    """Saldo de cuentas por cobrar (clase 13) a 31-dic por deudor y concepto."""
    _require_company(db, company_nit)
    rows = _tercero_movimientos(db, company_nit, year, "^13", cumulative=True)

    aggregated: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        concepto = _classify_1008(row.cuenta_puc)
        key = (row.tercero_nit, concepto)
        if key not in aggregated:
            ident = _tercero_identity(row.tercero_nit, row.tercero_nombre)
            aggregated[key] = {
                "formato": "1008",
                "concepto": concepto,
                **{k: v for k, v in ident.items() if not k.startswith("_")},
                "saldo_cuentas_por_cobrar": 0,
                "listo_para_envio": "Sí" if ident["_submission_ready"] else "No",
                "errores_validacion": ident["_errors"],
            }
        # CxC son de naturaleza débito: saldo = débitos - créditos.
        aggregated[key]["saldo_cuentas_por_cobrar"] += int(
            round(float(row.total_debito) - float(row.total_credito))
        )

    return [r for r in aggregated.values() if r["saldo_cuentas_por_cobrar"] != 0]


# ---------------------------------------------------------------------------
# Formato 1009 — Saldo de cuentas por pagar a 31-12
# ---------------------------------------------------------------------------


def generate_formato_1009(
    db: Session, company_nit: str, year: int
) -> List[Dict[str, Any]]:
    """Saldo de cuentas por pagar (clases 21/22/23) a 31-dic por acreedor."""
    _require_company(db, company_nit)
    rows = _tercero_movimientos(db, company_nit, year, "^2[123]", cumulative=True)

    aggregated: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        concepto = _classify_1009(row.cuenta_puc)
        key = (row.tercero_nit, concepto)
        if key not in aggregated:
            ident = _tercero_identity(row.tercero_nit, row.tercero_nombre)
            aggregated[key] = {
                "formato": "1009",
                "concepto": concepto,
                **{k: v for k, v in ident.items() if not k.startswith("_")},
                "saldo_cuentas_por_pagar": 0,
                "listo_para_envio": "Sí" if ident["_submission_ready"] else "No",
                "errores_validacion": ident["_errors"],
            }
        # CxP son de naturaleza crédito: saldo = créditos - débitos.
        aggregated[key]["saldo_cuentas_por_pagar"] += int(
            round(float(row.total_credito) - float(row.total_debito))
        )

    return [r for r in aggregated.values() if r["saldo_cuentas_por_pagar"] != 0]
