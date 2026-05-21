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

# Venta-side PUC accounts (the company is the seller).
CUENTA_IVA_GENERADO = "240805"  # IVA generado (pasivo a DIAN, crédito)
CUENTA_CLIENTES_NACIONALES = "130505"  # Cuentas por cobrar — clientes nacionales
CUENTA_RETEFTE_RECIBIDA = "135515"  # Autorretenciones / retefuente a favor (activo)
CUENTA_RETEICA_RECIBIDA = "135517"  # ReteICA a favor (activo)

# Doc types that flip the journal pattern to seller-side (factura venta).
_VENTA_DOC_TYPES = frozenset(
    {
        "factura_venta",
        "nota_debito_venta",
        "nota_credito_venta",
        "recibo_caja",
    }
)


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


# DIAN facturas electrónicas routinely apply centavos rounding on totals
# (e.g. subtotal+IVA exact $2,156,199.80 → factura total $2,156,200.00 with
# "Redondeo Aplicado: 0.20"). Tolerate up to $1 in journal balance — anything
# larger is a true partida-doble error worth blocking on.
_BALANCE_TOLERANCE = Decimal("1.00")


def _validate_balance(entries: List[JournalEntry], builder_name: str) -> None:
    total_debitos = sum(Decimal(e["debito"]) for e in entries)
    total_creditos = sum(Decimal(e["credito"]) for e in entries)
    diff = abs(total_debitos - total_creditos)
    if diff > _BALANCE_TOLERANCE:
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
        doc_type: str = "factura_compra",
        cuenta_reteica: str = CUENTA_RETEICA_POR_PAGAR,
    ) -> List[JournalEntry]:
        """Build journal entries for Via A (ingest path).

        The journal pattern flips based on ``doc_type``:

        * COMPRA-like (default): the company is the buyer.
          - Debit ``cuenta_puc`` for ``base = total - iva`` (gasto/activo).
          - Debit ``240802`` for ``iva`` (IVA descontable, activo recuperable).
          - Credit ``220505`` for ``total - retefuente - reteica`` (CxP).
          - Credit ``2365`` / ``2368`` for retenciones practicadas (pasivos).

        * VENTA-like (``doc_type`` in ``_VENTA_DOC_TYPES``): the company is the seller.
          - Credit ``cuenta_puc`` for ``base = total - iva`` (ingreso 4xxx).
          - Credit ``240805`` for ``iva`` (IVA generado, pasivo a DIAN).
          - Debit ``130505`` for ``total - retefuente - reteica`` (CxC neta).
          - Debit ``135515`` / ``135517`` for retenciones recibidas (anticipos a favor).

        Args:
            fecha: Transaction date.
            cuenta_puc: PUC account code for the operational gasto (compra) or
                ingreso (venta).
            puc_descripcion: Description for the PUC account.
            total: Total invoice amount (must be non-negative).
            iva: VAT amount (must be non-negative).
            retefuente: Withholding tax amount (must be non-negative).
            reteica: ICA withholding amount (must be non-negative).
            nit: Counterparty NIT.
            descripcion: Transaction description.
            doc_type: Document classification (e.g. ``factura_compra`` /
                ``factura_venta``). Defaults to ``factura_compra`` so call
                sites that do not yet pass ``doc_type`` keep the legacy
                buyer-side behaviour unchanged.

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

        if doc_type in _VENTA_DOC_TYPES:
            return JournalBuilder._build_from_ingest_venta(
                fecha=fecha,
                cuenta_puc=cuenta_puc,
                puc_descripcion=puc_descripcion,
                total=total,
                iva=iva,
                retefuente=retefuente,
                reteica=reteica,
                nit=nit,
                descripcion=descripcion,
            )
        return JournalBuilder._build_from_ingest_compra(
            fecha=fecha,
            cuenta_puc=cuenta_puc,
            puc_descripcion=puc_descripcion,
            total=total,
            iva=iva,
            retefuente=retefuente,
            reteica=reteica,
            nit=nit,
            descripcion=descripcion,
            cuenta_reteica=cuenta_reteica,
        )

    @staticmethod
    def _build_from_ingest_compra(
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
        cuenta_reteica: str = CUENTA_RETEICA_POR_PAGAR,
    ) -> List[JournalEntry]:
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
                    "cuenta": cuenta_reteica,
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
    def _build_from_ingest_venta(
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
        """Build seller-side journal entries (factura_venta and friends).

        Mirror of the compra path but with sides flipped:
        the client owes the FULL total c/IVA → debit CxC; ingreso is the base
        (subtotal sin IVA); IVA generado credits 240805 instead of debiting
        the descontable; retenciones recibidas are anticipos a favor (debit).
        """
        entries: List[JournalEntry] = []
        base = total - iva
        fecha_iso = _iso_fecha(fecha)

        cxc_amount = total - retefuente - reteica
        if cxc_amount < 0:
            raise ValueError(
                f"retenciones exceed total: total={total}, "
                f"retefuente={retefuente}, reteica={reteica}"
            )
        if cxc_amount > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_CLIENTES_NACIONALES,
                    "descripcion": "Clientes Nacionales",
                    "tercero_nit": nit,
                    "detalle": f"CxC {descripcion}",
                    "debito": str(cxc_amount),
                    "credito": "0",
                }
            )

        if retefuente > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_RETEFTE_RECIBIDA,
                    "descripcion": "Retefuente recibida (anticipo)",
                    "tercero_nit": nit,
                    "detalle": f"Retefuente recibida {descripcion}",
                    "debito": str(retefuente),
                    "credito": "0",
                }
            )

        if reteica > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_RETEICA_RECIBIDA,
                    "descripcion": "ReteICA recibida (anticipo)",
                    "tercero_nit": nit,
                    "detalle": f"ReteICA recibida {descripcion}",
                    "debito": str(reteica),
                    "credito": "0",
                }
            )

        if base > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": cuenta_puc,
                    "descripcion": puc_descripcion or descripcion,
                    "tercero_nit": nit,
                    "detalle": descripcion,
                    "debito": "0",
                    "credito": str(base),
                }
            )

        if iva > 0:
            entries.append(
                {
                    "fecha": fecha_iso,
                    "cuenta": CUENTA_IVA_GENERADO,
                    "descripcion": "IVA Generado",
                    "tercero_nit": nit,
                    "detalle": f"IVA generado por {descripcion}",
                    "debito": "0",
                    "credito": str(iva),
                }
            )

        _validate_balance(entries, "ingest_venta")
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
