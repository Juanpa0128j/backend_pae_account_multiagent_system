"""
Tests for the Schema Validation System.

Covers:
- Valid outputs for all 4 agents (Ingesta, Contador, Tributario, Auditor)
- Invalid outputs with specific field errors
- PUC code validation
- Double-entry integrity check
- Tax consistency checks
- Audit logic consistency checks
- Validation engine metrics tracking
- Retry logic and correction prompt generation
"""

import pytest
from datetime import date
from decimal import Decimal

from app.models.agent_outputs import (
    IngestOutput,
    ContadorOutput,
    TributarioOutput,
    AuditorOutput,
    NivelRiesgo,
    AGENT_OUTPUT_SCHEMAS,
)
from app.services.validation_engine import (
    OutputValidator,
    ValidationStatus,
)
from pydantic import ValidationError

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def valid_ingest_data() -> dict:
    return {
        "transactions": [
            {
                "fecha": "2026-01-15",
                "nit_emisor": "900123456",
                "nit_receptor": "800999888",
                "total": 150000.00,
                "descripcion": "Pago servicio de internet",
                "items": [],
            }
        ]
    }


@pytest.fixture
def valid_contador_data() -> dict:
    return {
        "fecha_registro": "2026-01-15",
        "tipo_documento": "factura",
        "descripcion_general": "Registro factura servicio internet Claro",
        "asientos": [
            {
                "cuenta_puc": "5135",
                "nombre_cuenta": "Servicios",
                "tipo_movimiento": "debito",
                "valor": 150000.00,
                "descripcion": "Gasto servicio internet",
            },
            {
                "cuenta_puc": "2205",
                "nombre_cuenta": "Proveedores nacionales",
                "tipo_movimiento": "credito",
                "valor": 150000.00,
                "descripcion": "Obligación con Claro Colombia",
            },
        ],
        "total_debitos": 150000.00,
        "total_creditos": 150000.00,
    }


@pytest.fixture
def valid_tributario_data() -> dict:
    return {
        "fecha_analisis": "2026-01-15",
        "documento_referencia": "FAC-2026-001",
        "aplica_impuestos": True,
        "impuestos": [
            {
                "tipo_impuesto": "IVA",
                "base_gravable": 150000.00,
                "tarifa_porcentaje": 19.0,
                "valor_impuesto": 28500.00,
                "cuenta_puc": "2408",
            }
        ],
        "total_impuestos": 28500.00,
        "observaciones": "IVA 19% aplicado sobre servicio gravado",
    }


@pytest.fixture
def valid_auditor_data() -> dict:
    return {
        "fecha_auditoria": "2026-01-16",
        "documento_referencia": "FAC-2026-001",
        "aprobado": True,
        "nivel_riesgo": "bajo",
        "hallazgos": [],
        "puntaje_calidad": 95.0,
        "resumen": "Documento procesado correctamente sin hallazgos relevantes.",
    }


@pytest.fixture
def validator() -> OutputValidator:
    return OutputValidator()


# =========================================================================
# 1. IngestOutput Tests
# =========================================================================


class TestIngestOutput:
    def test_valid_output(self, valid_ingest_data):
        output = IngestOutput.model_validate(valid_ingest_data)
        assert len(output.transactions) == 1
        tx = output.transactions[0]
        assert tx.fecha == date(2026, 1, 15)
        assert tx.total == Decimal("150000.00")
        assert tx.nit_emisor == "900123456"

    def test_missing_required_field_nit_emisor(self, valid_ingest_data):
        del valid_ingest_data["transactions"][0]["nit_emisor"]
        with pytest.raises(ValidationError) as exc_info:
            IngestOutput.model_validate(valid_ingest_data)
        assert "nit_emisor" in str(exc_info.value)

    def test_invalid_date_format(self, valid_ingest_data):
        valid_ingest_data["transactions"][0]["fecha"] = "15/01/2026"
        with pytest.raises(ValidationError):
            IngestOutput.model_validate(valid_ingest_data)

    def test_negative_total(self, valid_ingest_data):
        valid_ingest_data["transactions"][0]["total"] = -100
        with pytest.raises(ValidationError):
            IngestOutput.model_validate(valid_ingest_data)

    def test_invalid_transactions_type(self):
        """transactions must be a list, not a string."""
        with pytest.raises(ValidationError):
            IngestOutput.model_validate({"transactions": "not-a-list"})

    def test_empty_transactions_allowed(self):
        """Empty transactions list is now allowed — new extraction methods return structured content."""
        output = IngestOutput.model_validate({"transactions": []})
        assert output.transactions == []

    def test_optional_fecha_null(self, valid_ingest_data):
        valid_ingest_data["transactions"][0]["fecha"] = None
        output = IngestOutput.model_validate(valid_ingest_data)
        assert output.transactions[0].fecha is None


# =========================================================================
# 2. ContadorOutput Tests
# =========================================================================


class TestContadorOutput:
    def test_valid_output(self, valid_contador_data):
        output = ContadorOutput.model_validate(valid_contador_data)
        assert len(output.asientos) == 2
        assert output.total_debitos == output.total_creditos

    def test_double_entry_violation(self, valid_contador_data):
        """Debits must equal credits."""
        valid_contador_data["asientos"][1]["valor"] = 100000.00
        valid_contador_data["total_creditos"] = 100000.00
        with pytest.raises(ValidationError, match="Double-entry violation"):
            ContadorOutput.model_validate(valid_contador_data)

    def test_invalid_puc_code(self, valid_contador_data):
        valid_contador_data["asientos"][0]["cuenta_puc"] = "ABCD"
        with pytest.raises(ValidationError, match="Invalid PUC code"):
            ContadorOutput.model_validate(valid_contador_data)

    def test_puc_code_too_long(self, valid_contador_data):
        valid_contador_data["asientos"][0]["cuenta_puc"] = "1234567"
        with pytest.raises(ValidationError, match="Invalid PUC code"):
            ContadorOutput.model_validate(valid_contador_data)

    def test_empty_asientos(self, valid_contador_data):
        valid_contador_data["asientos"] = []
        with pytest.raises(ValidationError):
            ContadorOutput.model_validate(valid_contador_data)

    def test_total_mismatch(self, valid_contador_data):
        valid_contador_data["total_debitos"] = 999999
        with pytest.raises(ValidationError, match="total_debitos"):
            ContadorOutput.model_validate(valid_contador_data)


# =========================================================================
# 3. TributarioOutput Tests
# =========================================================================


class TestTributarioOutput:
    def test_valid_output(self, valid_tributario_data):
        output = TributarioOutput.model_validate(valid_tributario_data)
        assert output.aplica_impuestos is True
        assert len(output.impuestos) == 1

    def test_aplica_true_but_no_impuestos(self, valid_tributario_data):
        valid_tributario_data["impuestos"] = []
        valid_tributario_data["total_impuestos"] = 0
        with pytest.raises(ValidationError, match="aplica_impuestos is True"):
            TributarioOutput.model_validate(valid_tributario_data)

    def test_aplica_false_but_has_impuestos(self, valid_tributario_data):
        valid_tributario_data["aplica_impuestos"] = False
        with pytest.raises(ValidationError, match="aplica_impuestos is False"):
            TributarioOutput.model_validate(valid_tributario_data)

    def test_total_mismatch(self, valid_tributario_data):
        valid_tributario_data["total_impuestos"] = 10000
        with pytest.raises(ValidationError, match="total_impuestos"):
            TributarioOutput.model_validate(valid_tributario_data)

    def test_tarifa_over_100(self, valid_tributario_data):
        valid_tributario_data["impuestos"][0]["tarifa_porcentaje"] = 150
        with pytest.raises(ValidationError):
            TributarioOutput.model_validate(valid_tributario_data)

    def test_no_taxes_valid(self):
        data = {
            "fecha_analisis": "2026-01-15",
            "documento_referencia": "FAC-2026-002",
            "aplica_impuestos": False,
            "impuestos": [],
            "total_impuestos": 0,
            "observaciones": None,
        }
        output = TributarioOutput.model_validate(data)
        assert output.aplica_impuestos is False
        assert len(output.impuestos) == 0


# =========================================================================
# 4. AuditorOutput Tests
# =========================================================================


class TestAuditorOutput:
    def test_valid_output(self, valid_auditor_data):
        output = AuditorOutput.model_validate(valid_auditor_data)
        assert output.aprobado is True
        assert output.nivel_riesgo == NivelRiesgo.BAJO

    def test_approved_with_high_risk(self, valid_auditor_data):
        valid_auditor_data["nivel_riesgo"] = "alto"
        with pytest.raises(ValidationError, match="Cannot approve"):
            AuditorOutput.model_validate(valid_auditor_data)

    def test_approved_with_critical_finding(self, valid_auditor_data):
        valid_auditor_data["hallazgos"] = [
            {
                "codigo": "AUD-001",
                "severidad": "critico",
                "descripcion": "Discrepancia significativa en el monto registrado vs factura original",
                "campo_afectado": "monto",
                "recomendacion": "Revisar y corregir el monto contra el documento fuente original",
            }
        ]
        with pytest.raises(ValidationError, match="critical findings"):
            AuditorOutput.model_validate(valid_auditor_data)

    def test_invalid_finding_code(self, valid_auditor_data):
        valid_auditor_data["aprobado"] = False
        valid_auditor_data["nivel_riesgo"] = "medio"
        valid_auditor_data["hallazgos"] = [
            {
                "codigo": "WRONG-01",
                "severidad": "advertencia",
                "descripcion": "Un hallazgo con código inválido detectado",
                "recomendacion": "Corregir el formato del código de hallazgo",
            }
        ]
        with pytest.raises(ValidationError):
            AuditorOutput.model_validate(valid_auditor_data)

    def test_puntaje_out_of_range(self, valid_auditor_data):
        valid_auditor_data["puntaje_calidad"] = 150
        with pytest.raises(ValidationError):
            AuditorOutput.model_validate(valid_auditor_data)

    def test_rejected_with_findings(self):
        data = {
            "fecha_auditoria": "2026-01-16",
            "documento_referencia": "FAC-2026-001",
            "aprobado": False,
            "nivel_riesgo": "alto",
            "hallazgos": [
                {
                    "codigo": "AUD-001",
                    "severidad": "error",
                    "descripcion": "Monto no coincide con el documento escaneado original",
                    "campo_afectado": "monto",
                    "recomendacion": "Verificar el monto contra la fuente documental física",
                }
            ],
            "puntaje_calidad": 40.0,
            "resumen": "Documento rechazado por inconsistencias en el monto reportado.",
        }
        output = AuditorOutput.model_validate(data)
        assert output.aprobado is False
        assert output.nivel_riesgo == NivelRiesgo.ALTO


# =========================================================================
# 5. Validation Engine Tests
# =========================================================================


class TestOutputValidator:
    def test_validate_valid_ingest(self, validator, valid_ingest_data):
        result = validator.validate("ingesta", valid_ingest_data)
        assert result.is_valid
        assert result.status == ValidationStatus.VALID
        assert result.validated_output is not None

    def test_validate_invalid_ingest(self, validator):
        # total < 0 violates the ge=0 constraint on RawTransactionItem
        bad_data = {
            "transactions": [{"total": -999, "nit_emisor": "x", "nit_receptor": "y"}]
        }
        result = validator.validate("ingesta", bad_data)
        assert not result.is_valid
        assert result.status == ValidationStatus.INVALID
        assert len(result.errors) > 0

    def test_validate_unknown_agent(self, validator):
        result = validator.validate("unknown_agent", {})
        assert result.status == ValidationStatus.ERROR

    def test_compliance_rate_all_valid(self, validator, valid_ingest_data):
        for _ in range(5):
            validator.validate("ingesta", valid_ingest_data)
        assert validator.schema_compliance_rate() == 1.0

    def test_compliance_rate_mixed(self, validator, valid_ingest_data):
        # 3 valid + 2 invalid = 60% compliance
        for _ in range(3):
            validator.validate("ingesta", valid_ingest_data)
        invalid = {
            "transactions": [{"total": -1, "nit_emisor": "x", "nit_receptor": "y"}]
        }
        for _ in range(2):
            validator.validate("ingesta", invalid)
        assert validator.schema_compliance_rate() == 0.6

    def test_compliance_rate_per_agent(
        self, validator, valid_ingest_data, valid_contador_data
    ):
        validator.validate("ingesta", valid_ingest_data)
        validator.validate("contador", {"bad": True})
        assert validator.schema_compliance_rate("ingesta") == 1.0
        assert validator.schema_compliance_rate("contador") == 0.0

    def test_should_retry(self, validator):
        invalid = {
            "transactions": [{"total": -1, "nit_emisor": "x", "nit_receptor": "y"}]
        }
        result = validator.validate("ingesta", invalid, attempt=1)
        assert validator.should_retry(result) is True

    def test_should_not_retry_on_max_attempts(self, validator):
        invalid = {
            "transactions": [{"total": -1, "nit_emisor": "x", "nit_receptor": "y"}]
        }
        result = validator.validate("ingesta", invalid, attempt=3)
        assert validator.should_retry(result) is False

    def test_build_correction_prompt(self, validator):
        result = validator.validate("ingesta", {"bad": True}, attempt=1)
        prompt = validator.build_correction_prompt(result)
        assert "ERRORES ENCONTRADOS" in prompt
        assert "ESQUEMA ESPERADO" in prompt

    def test_get_metrics(self, validator, valid_ingest_data):
        invalid = {
            "transactions": [{"total": -1, "nit_emisor": "x", "nit_receptor": "y"}]
        }
        validator.validate("ingesta", valid_ingest_data)
        validator.validate("ingesta", invalid)
        metrics = validator.get_metrics()
        assert metrics["total_validations"] == 2
        assert metrics["total_passed"] == 1
        assert metrics["total_failed"] == 1
        assert metrics["overall_compliance_rate"] == 0.5
        assert "ingesta" in metrics["per_agent_detail"]

    def test_reset_metrics(self, validator, valid_ingest_data):
        validator.validate("ingesta", valid_ingest_data)
        validator.reset_metrics()
        metrics = validator.get_metrics()
        assert metrics["total_validations"] == 0

    def test_error_summary_format(self, validator):
        invalid = {
            "transactions": [{"total": -1, "nit_emisor": "x", "nit_receptor": "y"}]
        }
        result = validator.validate("ingesta", invalid)
        summary = result.error_summary()
        assert "ingesta" in summary
        assert "attempt" in summary


# =========================================================================
# 6. Schema Registry Tests
# =========================================================================


class TestSchemaRegistry:
    def test_all_agents_registered(self):
        # Core processing agents must be present
        core = {"ingesta", "contador", "tributario", "auditor"}
        # Reportero report-type schemas (not used in retry-validation loop)
        reportero = {
            "reportero_balance",
            "reportero_pnl",
            "reportero_cashflow",
            "reportero_iva",
            "reportero_withholdings",
        }
        registered = set(AGENT_OUTPUT_SCHEMAS.keys())
        assert core.issubset(registered)
        assert reportero.issubset(registered)

    def test_schemas_are_pydantic_models(self):
        from pydantic import BaseModel

        for name, schema_cls in AGENT_OUTPUT_SCHEMAS.items():
            assert issubclass(
                schema_cls, BaseModel
            ), f"Schema for '{name}' is not a Pydantic BaseModel"

    def test_schemas_produce_json_schema(self):
        """All schemas should export a valid JSON schema."""
        for name, schema_cls in AGENT_OUTPUT_SCHEMAS.items():
            js = schema_cls.model_json_schema()
            assert "properties" in js, f"JSON schema for '{name}' has no 'properties'"
