"""Data-driven repair of LLM/OCR row-shift errors in bank statement movements.

The `saldo` column in a Colombian bank statement is cumulative: each row's
saldo equals the previous saldo plus credito minus debito. When LLM/OCR
misaligns columns (a common failure on dense tabular layouts), the
`credito`/`debito` columns get shifted between rows but the `saldo` column
typically does not — saldo is visually anchored to the right and easier to
parse as a single column.

This module recomputes credito/debito deltas from the saldo column and
overwrites the row when the LLM-extracted values produce an inconsistent
running balance.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

TOLERANCE_ROW = Decimal("1.00")
TOLERANCE_FINAL = Decimal("10.00")


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def repair_bank_movements(content: dict) -> tuple[dict, list[dict]]:
    """Repair row-shifted credito/debito values using the running saldo column.

    Returns the (possibly modified) content dict and a list of repair logs.
    Each repair log is a dict suitable for inclusion in the audit trace:
        {row, fecha, descripcion, before: {credito, debito},
         after: {credito, debito}, expected_delta}

    If global saldo_final boundary disagrees after repair, the repair is
    rolled back and a single global warning is emitted instead — the saldo
    column itself is suspect and we cannot trust it as ground truth.
    """
    movements = content.get("movements") or []
    if not movements:
        return content, []

    saldo_inicial = _to_decimal(content.get("saldo_inicial"))
    saldo_final_declared = _to_decimal(content.get("saldo_final"))

    repairs: list[dict] = []
    repaired_movements: list[dict] = []
    prev_saldo = saldo_inicial
    skip_global_check = saldo_inicial == 0

    for index, mov in enumerate(movements):
        mov_copy = dict(mov)
        credito = _to_decimal(mov_copy.get("credito"))
        debito = _to_decimal(mov_copy.get("debito"))
        actual_delta = credito - debito

        if mov_copy.get("saldo") in (None, ""):
            repaired_movements.append(mov_copy)
            prev_saldo = prev_saldo + actual_delta
            continue

        saldo = _to_decimal(mov_copy.get("saldo"))
        expected_delta = saldo - prev_saldo
        discrepancy = abs(expected_delta - actual_delta)

        if discrepancy > TOLERANCE_ROW:
            new_credito = expected_delta if expected_delta > 0 else Decimal("0")
            new_debito = abs(expected_delta) if expected_delta < 0 else Decimal("0")
            repairs.append(
                {
                    "row": index,
                    "fecha": mov_copy.get("fecha"),
                    "descripcion": mov_copy.get("descripcion") or "",
                    "before": {"credito": float(credito), "debito": float(debito)},
                    "after": {
                        "credito": float(new_credito),
                        "debito": float(new_debito),
                    },
                    "expected_delta": float(expected_delta),
                }
            )
            mov_copy["credito"] = float(new_credito)
            mov_copy["debito"] = float(new_debito)

        repaired_movements.append(mov_copy)
        prev_saldo = saldo

    if (
        not skip_global_check
        and saldo_final_declared != 0
        and abs(prev_saldo - saldo_final_declared) > TOLERANCE_FINAL
    ):
        logger.warning(
            "bank_statement_repair: saldo column unreliable "
            "(last_saldo=%s vs declared saldo_final=%s, diff=%s). "
            "Rolling back %d repair(s) and emitting global warning.",
            prev_saldo,
            saldo_final_declared,
            abs(prev_saldo - saldo_final_declared),
            len(repairs),
        )
        return content, [
            {
                "row": None,
                "warning": "saldo_column_unreliable",
                "last_saldo": float(prev_saldo),
                "declared_saldo_final": float(saldo_final_declared),
            }
        ]

    if repairs:
        logger.info(
            "bank_statement_repair: repaired %d row(s) from saldo column",
            len(repairs),
        )
        content = dict(content)
        content["movements"] = repaired_movements

    return content, repairs
