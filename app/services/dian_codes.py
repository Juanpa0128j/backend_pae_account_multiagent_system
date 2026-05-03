"""
DIAN nomenclature and lookup helpers.

This module exposes:

- ``NOMENCLATURA_DIAN``: official DIAN abbreviations for Colombian addresses
  (extracted from data/Nomenclatura_2012.pdf — the catalog has been stable
  since 2012, so a Python constant is the right home).

- ``expand_address(addr)``: replaces DIAN codes with their full Spanish form,
  for human-readable reports (e.g. ``CL 24 # 5-30`` → ``Calle 24 # 5-30``).

- ``normalize_address(addr)``: opposite direction — replaces full forms with
  DIAN codes, for tax declarations that require the abbreviated format
  (e.g. ``Calle 24 # 5-30`` → ``CL 24 # 5-30``).

- ``lookup_municipio(db, codigo)``: returns the row from the
  ``dian_municipios`` table for a given 5-digit DIAN code, or ``None``.

- ``lookup_municipio_by_name(db, nombre)``: case-insensitive search by name,
  returns the first match. Useful for parsing extractos where the bank only
  prints city names (e.g. "Sucursal CALI" → ``76001``).

The address helpers are pure-Python and have no DB dependency.
The municipio lookups require a SQLAlchemy session; pass the session in.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Nomenclature (DIAN, since 2012)
# ---------------------------------------------------------------------------

NOMENCLATURA_DIAN: dict[str, str] = {
    "AC": "Avenida calle",
    "AD": "Administración",
    "ADL": "Adelante",
    "AER": "Aeropuerto",
    "AG": "Agencia",
    "AGP": "Agrupación",
    "AK": "Avenida carrera",
    "AL": "Altillo",
    "ALD": "Al lado",
    "ALM": "Almacén",
    "AP": "Apartamento",
    "APTDO": "Apartado",
    "ATR": "Atrás",
    "AUT": "Autopista",
    "AV": "Avenida",
    "AVIAL": "Anillo vial",
    "BG": "Bodega",
    "BL": "Bloque",
    "BLV": "Boulevard",
    "BRR": "Barrio",
    "C": "Corregimiento",
    "CA": "Casa",
    "CAS": "Caserío",
    "CC": "Centro comercial",
    "CD": "Ciudadela",
    "CEL": "Célula",
    "CEN": "Centro",
    "CIR": "Circular",
    "CL": "Calle",
    "CLJ": "Callejón",
    "CN": "Camino",
    "CON": "Conjunto residencial",
    "CONJ": "Conjunto",
    "CR": "Carrera",
    "CRT": "Carretera",
    "CRV": "Circunvalar",
    "CS": "Consultorio",
    "DG": "Diagonal",
    "DP": "Depósito",
    "DPTO": "Departamento",
    "DS": "Depósito sótano",
    "ED": "Edificio",
    "EN": "Entrada",
    "ES": "Escalera",
    "ESQ": "Esquina",
    "ESTE": "Este",
    "ET": "Etapa",
    "EX": "Exterior",
    "FCA": "Finca",
    "GJ": "Garaje",
    "GS": "Garaje sótano",
    "GT": "Glorieta",
    "HC": "Hacienda",
    "HG": "Hangar",
    "IN": "Interior",
    "IP": "Inspección de Policía",
    "IPD": "Inspección Departamental",
    "IPM": "Inspección Municipal",
    "KM": "Kilómetro",
    "LC": "Local",
    "LM": "Local mezzanine",
    "LT": "Lote",
    "MD": "Módulo",
    "MJ": "Mojón",
    "MLL": "Muelle",
    "MN": "Mezzanine",
    "MZ": "Manzana",
    "NORTE": "Norte",
    "O": "Oriente",
    "OCC": "Occidente",
    "OESTE": "Oeste",
    "OF": "Oficina",
    "P": "Piso",
    "PA": "Parcela",
    "PAR": "Parque",
    "PD": "Predio",
    "PH": "Penthouse",
    "PJ": "Pasaje",
    "PL": "Planta",
    "PN": "Puente",
    "POR": "Portería",
    "POS": "Poste",
    "PQ": "Parqueadero",
    "PRJ": "Paraje",
    "PS": "Paseo",
    "PT": "Puesto",
    "PW": "Park Way",
    "RP": "Round Point",
    "SA": "Salón",
    "SC": "Salón comunal",
    "SD": "Salida",
    "SEC": "Sector",
    "SL": "Solar",
    "SM": "Súper manzana",
    "SS": "Semisótano",
    "ST": "Sótano",
    "SUITE": "Suite",
    "SUR": "Sur",
    "TER": "Terminal",
    "TERPLN": "Terraplén",
    "TO": "Torre",
    "TV": "Transversal",
    "TZ": "Terraza",
    "UN": "Unidad",
    "UR": "Unidad residencial",
    "URB": "Urbanización",
    "VRD": "Vereda",
    "VTE": "Variante",
    "ZF": "Zona franca",
    "ZN": "Zona",
}

# Reverse map for normalize_address; longer phrases first so multi-word
# replacements (e.g. "Avenida carrera" before "Avenida") resolve correctly.
_REVERSE_NOMENCLATURA: list[tuple[str, str]] = sorted(
    ((value, key) for key, value in NOMENCLATURA_DIAN.items()),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def expand_address(addr: Optional[str]) -> Optional[str]:
    """Replace DIAN abbreviations with their full Spanish form.

    Matching is case-insensitive on tokens but preserves rest of input.
    Longer codes (TERPLN, AVIAL) are matched first so that shorter codes
    sharing a prefix do not steal the match.
    """
    if not addr:
        return addr

    keys_by_length = sorted(NOMENCLATURA_DIAN.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(k) for k in keys_by_length) + r")\b",
        flags=re.IGNORECASE,
    )

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return NOMENCLATURA_DIAN.get(token.upper(), token)

    return pattern.sub(_replace, addr)


def normalize_address(addr: Optional[str]) -> Optional[str]:
    """Replace full Spanish forms with their DIAN abbreviation.

    Longer phrases match first so ``Avenida carrera`` resolves to ``AK``
    before ``Avenida`` resolves to ``AV``. Case-insensitive; output uses
    the canonical uppercase DIAN code.
    """
    if not addr:
        return addr

    result = addr
    for full, code in _REVERSE_NOMENCLATURA:
        result = re.sub(
            r"\b" + re.escape(full) + r"\b",
            code,
            result,
            flags=re.IGNORECASE,
        )
    return result


# ---------------------------------------------------------------------------
# Municipio lookups (dian_municipios table)
# ---------------------------------------------------------------------------

_LOOKUP_BY_CODIGO_SQL = text("""
    SELECT codigo, nombre, departamento_codigo, departamento_nombre
    FROM dian_municipios
    WHERE codigo = :codigo
    """)

_LOOKUP_BY_NAME_SQL = text("""
    SELECT codigo, nombre, departamento_codigo, departamento_nombre
    FROM dian_municipios
    WHERE upper(nombre) = upper(:nombre)
    LIMIT 1
    """)


def lookup_municipio(db: Session, codigo: Optional[str]) -> Optional[dict]:
    """Return the dian_municipios row for ``codigo``, or ``None`` if missing.

    ``codigo`` is normalised to a 5-character zero-padded string before
    querying so that integer-shaped inputs (``11001``) work too.
    """
    if codigo is None:
        return None
    normalized = str(codigo).zfill(5)
    if len(normalized) != 5 or not normalized.isdigit():
        return None
    row = db.execute(_LOOKUP_BY_CODIGO_SQL, {"codigo": normalized}).fetchone()
    if not row:
        return None
    return dict(row._mapping)


def lookup_municipio_by_name(db: Session, nombre: Optional[str]) -> Optional[dict]:
    """Return the first dian_municipios row where name matches case-insensitively."""
    if not nombre:
        return None
    row = db.execute(_LOOKUP_BY_NAME_SQL, {"nombre": nombre.strip()}).fetchone()
    if not row:
        return None
    return dict(row._mapping)
