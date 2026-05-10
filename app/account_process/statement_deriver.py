"""Pure function module that derives financial statement data from journal entries."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List


class StatementDeriver:
    @staticmethod
    def derive_balance_general(
        journal_entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Derive balance general data from journal entries."""
        totals: dict[int, Decimal] = {
            1: Decimal("0"),
            2: Decimal("0"),
            3: Decimal("0"),
            4: Decimal("0"),
            5: Decimal("0"),
            6: Decimal("0"),
        }
        account_balances: dict[str, Decimal] = {}

        for entry in journal_entries:
            cuenta = str(entry.get("cuenta", ""))
            if not cuenta or not cuenta[0].isdigit():
                continue

            clase = int(cuenta[0])
            if clase not in totals:
                continue

            debito = Decimal(str(entry.get("debito", "0") or "0"))
            credito = Decimal(str(entry.get("credito", "0") or "0"))

            if clase in (1, 5, 6):  # Debit nature
                saldo = debito - credito
            else:  # Credit nature (2, 3, 4)
                saldo = credito - debito

            totals[clase] += saldo
            account_balances[cuenta] = (
                account_balances.get(cuenta, Decimal("0")) + saldo
            )

        utilidad_neta = totals[4] - totals[5] - totals[6]
        total_activos = totals[1]
        total_pasivos = totals[2]
        patrimonio_sin_utilidad = totals[3]
        total_patrimonio = patrimonio_sin_utilidad + utilidad_neta
        cuadre = total_activos == total_pasivos + total_patrimonio

        cuentas = [
            {"cuenta": k, "saldo": v} for k, v in sorted(account_balances.items())
        ]

        return {
            "total_activos": total_activos,
            "total_pasivos": total_pasivos,
            "total_patrimonio": total_patrimonio,
            "utilidad_neta": utilidad_neta,
            "patrimonio_sin_utilidad": patrimonio_sin_utilidad,
            "cuadre": cuadre,
            "cuentas": cuentas,
        }

    @staticmethod
    def derive_estado_resultados(
        journal_entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Derive estado de resultados data from journal entries."""
        totals: dict[int, Decimal] = {
            4: Decimal("0"),
            5: Decimal("0"),
            6: Decimal("0"),
        }
        detail: dict[int, dict[str, Decimal]] = {
            4: {},
            5: {},
            6: {},
        }

        for entry in journal_entries:
            cuenta = str(entry.get("cuenta", ""))
            if not cuenta or not cuenta[0].isdigit():
                continue

            clase = int(cuenta[0])
            if clase not in totals:
                continue

            debito = Decimal(str(entry.get("debito", "0") or "0"))
            credito = Decimal(str(entry.get("credito", "0") or "0"))

            if clase == 4:  # Credit nature
                saldo = credito - debito
            else:  # Debit nature (5, 6)
                saldo = debito - credito

            totals[clase] += saldo
            detail[clase][cuenta] = detail[clase].get(cuenta, Decimal("0")) + saldo

        utilidad_bruta = totals[4] - totals[6]
        utilidad_neta = utilidad_bruta - totals[5]

        def _build_items(class_detail: dict[str, Decimal]) -> list[dict[str, Any]]:
            return [{"cuenta": k, "saldo": v} for k, v in sorted(class_detail.items())]

        return {
            "ingresos": _build_items(detail[4]),
            "gastos": _build_items(detail[5]),
            "costo_ventas": _build_items(detail[6]),
            "utilidad_bruta": utilidad_bruta,
            "utilidad_neta": utilidad_neta,
        }
