"""
Strict Pydantic schemas for all agent outputs.

Every agent (Ingesta, Contador, Tributario, Auditor) MUST produce output
that validates against these schemas. Non-compliant outputs are rejected
by the Supervisor and re-sent for correction.

Colombian accounting standards (PUC codes, DIAN tax codes) are validated.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, List, Optional

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    ConfigDict,
)


# ---------------------------------------------------------------------------
# Shared enums & constants
# ---------------------------------------------------------------------------

class TipoDocumento(str, Enum):
    RECIBO = "recibo"
    FACTURA = "factura"
    EXTRACTO = "extracto"
    NOTA_CREDITO = "nota_credito"
    NOTA_DEBITO = "nota_debito"
    COMPROBANTE_EGRESO = "comprobante_egreso"
    OTRO = "otro"


class TipoMovimiento(str, Enum):
    DEBITO = "debito"
    CREDITO = "credito"


class TipoImpuesto(str, Enum):
    IVA = "IVA"
    RETEFUENTE = "retefuente"
    RETEIVA = "reteiva"
    RETEICA = "reteica"
    TIMBRE = "timbre"
    OTRO = "otro"


class NivelRiesgo(str, Enum):
    BAJO = "bajo"
    MEDIO = "medio"
    ALTO = "alto"
    CRITICO = "critico"


class SeveridadHallazgo(str, Enum):
    INFO = "info"
    ADVERTENCIA = "advertencia"
    ERROR = "error"
    CRITICO = "critico"


# PUC code regex: 1-6 digits (Colombian Plan Único de Cuentas)
PUC_PATTERN = re.compile(r"^\d{1,6}$")


# ---------------------------------------------------------------------------
# Helper validators
# ---------------------------------------------------------------------------

def _validate_puc_code(v: str) -> str:
    """Validate a Colombian PUC (Plan Único de Cuentas) code."""
    if not PUC_PATTERN.match(v):
        raise ValueError(
            f"Invalid PUC code '{v}'. Must be 1-6 digits "
            "(e.g. '1105' for Caja, '2205' for Proveedores)."
        )
    return v


def _parse_date(v: str | date | None) -> date | None:
    """Accept ISO date strings and convert to date objects."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(
                f"Invalid date '{v}'. Expected ISO format YYYY-MM-DD."
            )
    raise ValueError(f"Cannot parse date from type {type(v)}")


# ---------------------------------------------------------------------------
# 1. INGESTA Agent Output
# ---------------------------------------------------------------------------

class IngestOutput(BaseModel):
    """
    Schema for the Ingesta (Ingest) agent output.
    Represents structured data extracted from a receipt/invoice PDF.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha: date = Field(
        ..., description="Document date in YYYY-MM-DD format"
    )
    monto: Decimal = Field(
        ..., ge=0, description="Total amount (must be >= 0)"
    )
    concepto: str = Field(
        ..., min_length=3, max_length=500,
        description="Payment description / concept"
    )
    beneficiario: str = Field(
        ..., min_length=2, max_length=300,
        description="Payment recipient name"
    )
    empresa: str = Field(
        ..., min_length=2, max_length=300,
        description="Issuing company / bank"
    )
    referencia: Optional[str] = Field(
        None, max_length=100,
        description="Transaction reference number"
    )
    tipo_documento: TipoDocumento = Field(
        ..., description="Document type classification"
    )

    # -- validators --
    @field_validator("fecha", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)


# ---------------------------------------------------------------------------
# 2. CONTADOR Agent Output
# ---------------------------------------------------------------------------

class AsientoContable(BaseModel):
    """Single accounting entry (journal line)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    cuenta_puc: str = Field(
        ..., description="PUC account code (1-6 digits)"
    )
    nombre_cuenta: str = Field(
        ..., min_length=2, max_length=200,
        description="Account name"
    )
    tipo_movimiento: TipoMovimiento = Field(
        ..., description="Debit or Credit"
    )
    valor: Decimal = Field(
        ..., ge=0, description="Entry amount"
    )
    descripcion: Optional[str] = Field(
        None, max_length=500,
        description="Optional line description"
    )

    @field_validator("cuenta_puc")
    @classmethod
    def validate_puc(cls, v):  # noqa: N805
        return _validate_puc_code(v)


class ContadorOutput(BaseModel):
    """
    Schema for the Contador (Accountant) agent output.
    Produces classified journal entries following Colombian PUC.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_registro: date = Field(
        ..., description="Accounting registration date"
    )
    tipo_documento: TipoDocumento = Field(
        ..., description="Source document type"
    )
    descripcion_general: str = Field(
        ..., min_length=5, max_length=500,
        description="General description of the accounting event"
    )
    asientos: List[AsientoContable] = Field(
        ..., min_length=1,
        description="Journal entries (at least one)"
    )
    total_debitos: Decimal = Field(
        ..., ge=0, description="Sum of all debit entries"
    )
    total_creditos: Decimal = Field(
        ..., ge=0, description="Sum of all credit entries"
    )

    @field_validator("fecha_registro", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @model_validator(mode="after")
    def check_double_entry(self) -> "ContadorOutput":
        """Verify debits == credits (partida doble)."""
        debits = sum(
            a.valor for a in self.asientos
            if a.tipo_movimiento == TipoMovimiento.DEBITO
        )
        credits_ = sum(
            a.valor for a in self.asientos
            if a.tipo_movimiento == TipoMovimiento.CREDITO
        )
        if debits != credits_:
            raise ValueError(
                f"Double-entry violation: debits ({debits}) != credits ({credits_}). "
                "Each transaction must balance."
            )
        if self.total_debitos != debits:
            raise ValueError(
                f"total_debitos ({self.total_debitos}) does not match "
                f"sum of debit asientos ({debits})."
            )
        if self.total_creditos != credits_:
            raise ValueError(
                f"total_creditos ({self.total_creditos}) does not match "
                f"sum of credit asientos ({credits_})."
            )
        return self


# ---------------------------------------------------------------------------
# 3. TRIBUTARIO Agent Output
# ---------------------------------------------------------------------------

class DetalleImpuesto(BaseModel):
    """Detail of a single tax applied to the transaction."""

    model_config = ConfigDict(str_strip_whitespace=True)

    tipo_impuesto: TipoImpuesto = Field(
        ..., description="Tax type"
    )
    base_gravable: Decimal = Field(
        ..., ge=0, description="Taxable base amount"
    )
    tarifa_porcentaje: Decimal = Field(
        ..., ge=0, le=100,
        description="Tax rate as percentage (0-100)"
    )
    valor_impuesto: Decimal = Field(
        ..., ge=0, description="Calculated tax amount"
    )
    cuenta_puc: Optional[str] = Field(
        None, description="PUC account for this tax entry"
    )

    @field_validator("cuenta_puc")
    @classmethod
    def validate_puc(cls, v):  # noqa: N805
        if v is not None:
            return _validate_puc_code(v)
        return v


class TributarioOutput(BaseModel):
    """
    Schema for the Tributario (Tax) agent output.
    Analyses tax implications per Colombian DIAN regulations.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_analisis: date = Field(
        ..., description="Date the tax analysis was performed"
    )
    documento_referencia: str = Field(
        ..., min_length=1, max_length=100,
        description="Reference to the source document/transaction"
    )
    aplica_impuestos: bool = Field(
        ..., description="Whether taxes apply to this transaction"
    )
    impuestos: List[DetalleImpuesto] = Field(
        default_factory=list,
        description="List of applicable taxes"
    )
    total_impuestos: Decimal = Field(
        ..., ge=0,
        description="Sum of all tax amounts"
    )
    observaciones: Optional[str] = Field(
        None, max_length=1000,
        description="Additional tax observations or notes"
    )

    @field_validator("fecha_analisis", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @model_validator(mode="after")
    def check_tax_consistency(self) -> "TributarioOutput":
        """Validate internal tax consistency."""
        if self.aplica_impuestos and len(self.impuestos) == 0:
            raise ValueError(
                "aplica_impuestos is True but no impuestos were provided."
            )
        if not self.aplica_impuestos and len(self.impuestos) > 0:
            raise ValueError(
                "aplica_impuestos is False but impuestos were provided."
            )
        calculated_total = sum(i.valor_impuesto for i in self.impuestos)
        if self.total_impuestos != calculated_total:
            raise ValueError(
                f"total_impuestos ({self.total_impuestos}) does not match "
                f"sum of individual taxes ({calculated_total})."
            )
        return self


# ---------------------------------------------------------------------------
# 4. AUDITOR Agent Output
# ---------------------------------------------------------------------------

class HallazgoAuditoria(BaseModel):
    """Single audit finding/observation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    codigo: str = Field(
        ..., pattern=r"^AUD-\d{3,6}$",
        description="Finding code (e.g. AUD-001)"
    )
    severidad: SeveridadHallazgo = Field(
        ..., description="Finding severity level"
    )
    descripcion: str = Field(
        ..., min_length=10, max_length=1000,
        description="Detailed description of the finding"
    )
    campo_afectado: Optional[str] = Field(
        None, max_length=200,
        description="Field or account affected"
    )
    recomendacion: str = Field(
        ..., min_length=10, max_length=1000,
        description="Recommended corrective action"
    )


class AuditorOutput(BaseModel):
    """
    Schema for the Auditor agent output.
    Produces structured audit results with findings and risk assessment.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_auditoria: date = Field(
        ..., description="Date the audit was performed"
    )
    documento_referencia: str = Field(
        ..., min_length=1, max_length=100,
        description="Reference to the audited document"
    )
    aprobado: bool = Field(
        ..., description="Whether the document passes audit"
    )
    nivel_riesgo: NivelRiesgo = Field(
        ..., description="Overall risk level assessment"
    )
    hallazgos: List[HallazgoAuditoria] = Field(
        default_factory=list,
        description="List of audit findings"
    )
    puntaje_calidad: Decimal = Field(
        ..., ge=0, le=100,
        description="Quality score 0-100"
    )
    resumen: str = Field(
        ..., min_length=10, max_length=2000,
        description="Executive summary of the audit"
    )

    @field_validator("fecha_auditoria", mode="before")
    @classmethod
    def parse_fecha(cls, v):  # noqa: N805
        return _parse_date(v)

    @model_validator(mode="after")
    def check_audit_consistency(self) -> "AuditorOutput":
        """Validate audit logic consistency."""
        if self.aprobado and self.nivel_riesgo in (
            NivelRiesgo.ALTO, NivelRiesgo.CRITICO
        ):
            raise ValueError(
                "Cannot approve a document with high/critical risk level."
            )
        critical_findings = [
            h for h in self.hallazgos
            if h.severidad == SeveridadHallazgo.CRITICO
        ]
        if self.aprobado and len(critical_findings) > 0:
            raise ValueError(
                "Cannot approve a document with critical findings."
            )
        return self


# ---------------------------------------------------------------------------
# Registry: maps agent names to their output schema
# ---------------------------------------------------------------------------

AGENT_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "ingesta": IngestOutput,
    "contador": ContadorOutput,
    "tributario": TributarioOutput,
    "auditor": AuditorOutput,
}
