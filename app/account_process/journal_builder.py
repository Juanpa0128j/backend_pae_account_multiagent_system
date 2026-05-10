"""Pure function module that builds double-entry journal entries.

Extracted from app.agents.persist_node to keep the core accounting logic
free of DB/session dependencies.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, TypedDict

CUENTA_IVA_DESCONTABLE = "240802"
CUENTA_PROVEEDORES_NACIONALES = "220505"
CUENTA_RETEFUENTE_POR_PAGAR = "2365"
CUENTA_RETEICA_POR_PAGAR = "2368"


class JournalEntry(TypedDict):
    fecha: str
    cuenta: str
    descripcion: str
    tercero_nit: str
    detalle: str
    debito: str
    credito: str


def _iso_fecha(fecha: datetime) -> str:
    return fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)


def _validate_balance(entries: List[JournalEntry], builder_name: str) -> None:
    total_debitos = sum(Decimal(e["debito"]) for e in entries)
    total_creditos = sum(Decimal(e["credito"]) for e in entries)
    if total_debitos != total_creditos:
        raise ValueError(
            f"Unbalanced journal entries ({builder_name}): D={total_debitos} C={total_creditos}"
        )


class JournalBuilder:
    @staticmethod
    def build_from_ingest(
        *,
        fecha: datetime,
        cuenta_puc: str,
        puc_descripcion: str,
        total: Decimal,
        iva: Decimal,
        retefuente: Decimal,
        reteica: Decimal,
        nit: str,
        descripcion: str,
    ) -> List[JournalEntry]:
        """Build journal entries for Via A (ingest path).

        Args:
            fecha: Transaction date.
            cuenta_puc: PUC account code for the expense/asset.
            puc_descripcion: Description for the PUC account.
            total: Total invoice amount (must be non-negative).
            iva: VAT amount (must be non-negative).
            retefuente: Withholding tax amount (must be non-negative).
            reteica: ICA withholding amount (must be non-negative).
            nit: Counterparty NIT.
            descripcion: Transaction description.

        Returns:
            List of journal entries.

        Raises:
            ValueError: If any monetary value is negative, if retenciones
                exceed total, or if resulting entries are unbalanced.
        """
        for label, value in (
            ("total", total),
            ("iva", iva),
            ("retefuente", retefuente),
            ("reteica", reteica),
        ):
            if value < 0:
                raise ValueError(f"{label} must be non-negative, got {value}")

        entries: List[JournalEntry] = []
        base = total - iva
        fecha_iso = _iso_fecha(fecha)

        if base > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": cuenta_puc,
                    "descripcion": puc_descripcion or descripcion,
                    "tercero_nit": nit,
                    "detalle": descripcion,
                    "debito": str(base),
                    "credito": "0",
                }
            )

        if iva > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_IVA_DESCONTABLE,
                    "descripcion": "IVA Descontable",
                    "tercero_nit": nit,
                    "detalle": f"IVA por {descripcion}",
                    "debito": str(iva),
                    "credito": "0",
                }
            )

        total_credito_proveedor = total - retefuente - reteica
        if total_credito_proveedor < 0:
            raise ValueError(
                f"retenciones exceed total: total={total}, "
                f"retefuente={retefuente}, reteica={reteica}"
            )
        if total_credito_proveedor > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_PROVEEDORES_NACIONALES,
                    "descripcion": "Proveedores Nacionales",
                    "tercero_nit": nit,
                    "detalle": f"CxP {descripcion}",
                    "debito": "0",
                    "credito": str(total_credito_proveedor),
                }
            )

        if retefuente > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_RETEFUENTE_POR_PAGAR,
                    "descripcion": "Retencion en la Fuente por pagar",
                    "tercero_nit": nit,
                    "detalle": f"Retefuente {descripcion}",
                    "debito": "0",
                    "credito": str(retefuente),
                }
            )

        if reteica > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_RETEICA_POR_PAGAR,
                    "descripcion": "Retencion ICA por pagar",
                    "tercero_nit": nit,
                    "detalle": f"ReteICA {descripcion}",
                    "debito": "0",
                    "credito": str(reteica),
                }
            )

        _validate_balance(entries, "ingest")
        return entries

    @staticmethod
    def build_from_contador(
        *,
        fecha: datetime,
        asientos: List[Dict[str, Any]],
        nit: str,
        descripcion: str,
    ) -> List[JournalEntry]:
        """Build journal entries from contador output (process path).

        Args:
            fecha: Transaction date.
            asientos: List of raw accounting entries from the contador.
            nit: Counterparty NIT.
            descripcion: Fallback transaction description.

        Returns:
            List of journal entries.

        Raises:
            ValueError: If any asiento has a negative valor, or if resulting
                entries are unbalanced.
        """
        fecha_iso = _iso_fecha(fecha)
        entries: List[JournalEntry] = []
        for asiento in asientos:
            tipo = str(asiento.get("tipo_movimiento", "")).lower()
            valor = Decimal(str(asiento.get("valor") or "0"))
            if valor < 0:
                raise ValueError(f"valor must be non-negative, got {valor}")
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": str(asiento.get("cuenta_puc", "")),
                    "descripcion": asiento.get("nombre_cuenta") or descripcion,
                    "tercero_nit": nit,
                    "detalle": asiento.get("descripcion") or descripcion,
                    "debito": str(valor if tipo == "debito" else Decimal("0")),
                    "credito": str(valor if tipo == "credito" else Decimal("0")),
                }
            )

        _validate_balance(entries, "contador")
        return entries
