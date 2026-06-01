"""Tests for app/services/bank_statement_repair.py."""

from app.services.bank_statement_repair import repair_bank_movements


def _mov(fecha, desc, credito=0, debito=0, saldo=None):
    return {
        "fecha": fecha,
        "descripcion": desc,
        "credito": credito,
        "debito": debito,
        "saldo": saldo,
    }


class TestRepairBankMovements:
    def test_aligned_input_makes_no_repairs(self):
        content = {
            "saldo_inicial": 1000.00,
            "saldo_final": 1200.00,
            "movements": [
                _mov("2026-01-01", "ABONO", credito=100, saldo=1100),
                _mov("2026-01-02", "ABONO", credito=100, saldo=1200),
            ],
        }
        repaired, repairs = repair_bank_movements(content)
        assert repairs == []
        assert repaired["movements"][0]["credito"] == 100
        assert repaired["movements"][1]["credito"] == 100

    def test_row_shifted_credito_repaired_from_saldo(self):
        """LLM shifted credito column up by one row (saldo column intact).

        Real failure mode from Bancolombia extract:
        - Doc row 0: 04/01 ABONO INTERESES 103.24, saldo 18,845,921.88
        - Doc row 1: 05/01 PAGO MADECENTRO 2,353,292, saldo 21,199,213.88
        - Doc row 2: 05/01 ABONO INTERESES 29.04, saldo 21,199,242.92
        LLM shifted credito up by one row, leaving saldo column correct.
        """
        content = {
            "saldo_inicial": 18845818.64,
            "saldo_final": 21199242.92,
            "movements": [
                _mov(
                    "2026-01-04",
                    "ABONO INTERESES",
                    credito=2353292.00,
                    saldo=18845921.88,
                ),
                _mov("2026-01-05", "PAGO MADECENTRO", credito=29.04, saldo=21199213.88),
                _mov(
                    "2026-01-05", "ABONO INTERESES", credito=9999.99, saldo=21199242.92
                ),
            ],
        }
        repaired, repairs = repair_bank_movements(content)
        assert len(repairs) == 3
        assert repaired["movements"][0]["credito"] == 103.24
        assert repaired["movements"][1]["credito"] == 2353292.00
        assert repaired["movements"][2]["credito"] == 29.04

    def test_debito_repaired_when_expected_delta_negative(self):
        content = {
            "saldo_inicial": 1000.00,
            "saldo_final": 900.00,
            "movements": [
                _mov("2026-01-01", "PAGO", credito=0, debito=100, saldo=900),
            ],
        }
        repaired, repairs = repair_bank_movements(content)
        assert repairs == []
        assert repaired["movements"][0]["debito"] == 100

    def test_saldo_final_mismatch_rolls_back_repairs(self):
        """Unreliable saldo column → no repair, emit global warning."""
        content = {
            "saldo_inicial": 1000.00,
            "saldo_final": 9999.00,
            "movements": [
                _mov("2026-01-01", "X", credito=50, saldo=1100),
            ],
        }
        repaired, repairs = repair_bank_movements(content)
        assert len(repairs) == 1
        assert repairs[0]["warning"] == "saldo_column_unreliable"
        assert repaired["movements"][0]["credito"] == 50

    def test_empty_movements_returns_empty_repairs(self):
        content = {"saldo_inicial": 0, "saldo_final": 0, "movements": []}
        repaired, repairs = repair_bank_movements(content)
        assert repairs == []
        assert repaired["movements"] == []

    def test_missing_saldo_per_row_is_skipped(self):
        content = {
            "saldo_inicial": 1000.00,
            "saldo_final": 1150.00,
            "movements": [
                _mov("2026-01-01", "A", credito=50, saldo=None),
                _mov("2026-01-02", "B", credito=100, saldo=1150),
            ],
        }
        _, repairs = repair_bank_movements(content)
        assert len(repairs) == 0

    def test_saldo_inicial_zero_skips_global_check(self):
        content = {
            "saldo_inicial": 0,
            "saldo_final": 0,
            "movements": [
                _mov("2026-01-01", "X", credito=999, saldo=100),
                _mov("2026-01-02", "Y", credito=999, saldo=200),
            ],
        }
        repaired, repairs = repair_bank_movements(content)
        assert len(repairs) == 2
        assert repaired["movements"][0]["credito"] == 100.0
        assert repaired["movements"][1]["credito"] == 100.0
