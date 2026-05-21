from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def as_str(value: Any, default: str = "") -> str:
    """Normalize possibly-ORM values to plain strings."""
    if value is None:
        return default
    return str(value)


def sanitize_for_json(value: Any) -> Any:
    """Recursively convert non-JSON-serializable types to safe types."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_json(v) for v in value]
    return value


def safe_decimal(value: Any) -> Optional[Decimal]:
    """Safely parse a value into a Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_utc(parsed: datetime) -> datetime:
    """Normalize a parsed datetime to UTC.

    - tz-naive → assume the parsed components are already UTC, attach UTC tzinfo.
    - tz-aware UTC → return the value unchanged (preserves identity for callers
      and avoids an unnecessary copy).
    - tz-aware non-UTC → convert to UTC so persisted rows never mix offsets.
    """
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    if parsed.utcoffset() == timezone.utc.utcoffset(parsed):
        return parsed
    return parsed.astimezone(timezone.utc)


def safe_datetime(value: Any) -> Optional[datetime]:
    """Safely parse a value into a timezone-aware UTC datetime.

    Accepts a wide range of formats commonly produced by LLM extraction:
    - Full ISO 8601 with or without timezone (e.g. ``2026-01-06T10:26:58+00:00``
      or ``2026-01-06T10:26:58-05:00`` — any explicit offset is converted to UTC)
    - Date only (``2026-01-06``)
    - Month only (``2026-01``) → returns first day of the month
    - DD/MM/YYYY and DD-MM-YYYY (Colombian common formats)

    The return value is always tz-aware UTC; any explicit offset on the input
    is converted via ``astimezone(timezone.utc)`` so downstream queries can
    compare dates without mixed-offset bugs.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc(value)

    text = str(value).strip()
    if not text:
        return None

    # Prefer the stdlib fromisoformat for full ISO strings — handles timezone
    # offsets like "+00:00" and trailing "Z" (the latter only since Python 3.11).
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return _to_utc(parsed)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m",  # PILA / monthly tax periods → first day of month
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return _to_utc(parsed)
        except ValueError:
            continue
    return None


def infer_total_from_items(items: Any) -> Optional[Decimal]:
    """Best-effort total inference from extracted line items."""
    if not isinstance(items, list) or not items:
        return None

    inferred = Decimal("0")
    used_any = False

    for item in items:
        if not isinstance(item, dict):
            continue

        # Prefer explicit per-line totals when present.
        for key in ("valor_total_sin_impuesto", "valor_total", "total", "subtotal"):
            line_total = safe_decimal(item.get(key))
            if line_total is not None:
                inferred += line_total
                used_any = True
                break
        else:
            # Fallback to unit value if line total is absent.
            unit_value = safe_decimal(item.get("valor_unitario"))
            if unit_value is not None:
                qty = safe_decimal(item.get("cantidad"))
                if qty is not None and qty > 0 and qty <= Decimal("10000"):
                    inferred += unit_value * qty
                else:
                    inferred += unit_value
                used_any = True

    if not used_any:
        return None
    return inferred


def _derive_period_end(anio: int, periodicidad: str, periodo_numero: int) -> str:
    """Return the last day of the tax period as YYYY-MM-DD."""
    import calendar

    bimestral_end = {1: 2, 2: 4, 3: 6, 4: 8, 5: 10, 6: 12}
    cuatrimestral_end = {1: 4, 2: 8, 3: 12}

    if "bimestral" in periodicidad:
        month = bimestral_end.get(periodo_numero, 12)
    elif "cuatrimestral" in periodicidad:
        month = cuatrimestral_end.get(periodo_numero, 12)
    elif "mensual" in periodicidad:
        month = max(1, min(periodo_numero, 12))
    else:
        month = 12

    last_day = calendar.monthrange(anio, month)[1]
    return f"{anio}-{month:02d}-{last_day:02d}"


def build_structured_transactions(
    interpreted: dict[str, Any], doc_type: str
) -> list[dict[str, Any]]:
    """Map rich document schemas into one or more tx rows for persistence."""

    emisor = interpreted.get("emisor") or {}
    receptor = interpreted.get("receptor") or {}
    totales = interpreted.get("totales") or {}
    items_payload = interpreted.get("items") or interpreted.get("detalle_items") or []

    # --- Doc-type specific mapping ---
    if doc_type == "extracto_bancario":
        titular = interpreted.get("titular") or {}
        movements = interpreted.get("movements") or []
        txs: list[dict[str, Any]] = []

        if isinstance(movements, list):
            for movement in movements:
                if not isinstance(movement, dict):
                    continue

                debito = safe_decimal(movement.get("debito")) or Decimal("0")
                credito = safe_decimal(movement.get("credito")) or Decimal("0")
                # Bank statement convention: a `debito` value means the bank
                # debited (charged) the account = OUTFLOW; `credito` means the
                # bank credited (received) the account = INFLOW. Persist uses
                # this hint to invert the default outflow asiento when needed.
                if debito > Decimal("0"):
                    valor = debito
                    bank_direction = "salida"
                elif credito > Decimal("0"):
                    valor = credito
                    bank_direction = "entrada"
                else:
                    continue

                descripcion = as_str(movement.get("descripcion"), "Movimiento bancario")
                referencia = as_str(movement.get("referencia"), "").strip()
                if referencia:
                    descripcion = f"{descripcion} (ref: {referencia})"

                txs.append(
                    {
                        "fecha": movement.get("fecha")
                        or interpreted.get("periodo_fin")
                        or interpreted.get("periodo_inicio"),
                        "nit_emisor": as_str(
                            titular.get("nit") or interpreted.get("nit_emisor"), ""
                        ),
                        "nit_receptor": as_str(
                            interpreted.get("nit_receptor") or receptor.get("nit"), ""
                        ),
                        "total": str(valor),
                        "concepto": descripcion,
                        "descripcion": descripcion,
                        "bank_direction": bank_direction,
                        "items": [sanitize_for_json(movement)],
                    }
                )

        if txs:
            return txs

        resumen = interpreted.get("resumen") or {}
        fallback_total = (
            safe_decimal((resumen or {}).get("total_debitos"))
            or safe_decimal((resumen or {}).get("total_creditos"))
            or safe_decimal(interpreted.get("saldo_final"))
            or Decimal("0")
        )
        return [
            {
                "fecha": interpreted.get("periodo_fin")
                or interpreted.get("periodo_inicio"),
                "nit_emisor": as_str(titular.get("nit"), ""),
                "nit_receptor": as_str(
                    interpreted.get("nit_receptor") or receptor.get("nit"), ""
                ),
                "total": str(fallback_total),
                "concepto": "Extracto bancario",
                "descripcion": "Extracto bancario",
                "items": sanitize_for_json(
                    movements if isinstance(movements, list) else []
                ),
            }
        ]

    if doc_type == "nomina":
        empresa = interpreted.get("empresa") or {}
        periodo_inicio = as_str(interpreted.get("periodo_inicio"), "")
        periodo_fin = as_str(interpreted.get("periodo_fin"), "")
        periodo_txt = ""
        if periodo_inicio and periodo_fin:
            periodo_txt = f"Periodo {periodo_inicio} a {periodo_fin}"
        elif periodo_inicio:
            periodo_txt = f"Periodo desde {periodo_inicio}"
        elif periodo_fin:
            periodo_txt = f"Periodo hasta {periodo_fin}"

        raw_total = (
            interpreted.get("total_devengado")
            or interpreted.get("total_neto_pagar")
            or interpreted.get("total")
        )
        parsed_total = safe_decimal(raw_total)
        if parsed_total is None:
            empleados = interpreted.get("empleados") or []
            if isinstance(empleados, list):
                parsed_total = sum(
                    [
                        safe_decimal((e or {}).get("neto_pagar")) or Decimal("0")
                        for e in empleados
                        if isinstance(e, dict)
                    ],
                    Decimal("0"),
                )
            else:
                parsed_total = Decimal("0")

        concepto = "Nomina"
        if periodo_txt:
            concepto = f"Nomina - {periodo_txt}"

        raw_neto = safe_decimal(interpreted.get("total_neto_pagar"))
        raw_deducciones = safe_decimal(interpreted.get("total_deducciones"))

        return [
            {
                "fecha": interpreted.get("periodo_fin")
                or interpreted.get("periodo_inicio")
                or interpreted.get("fecha"),
                "nit_emisor": as_str(
                    empresa.get("nit") or interpreted.get("nit_emisor"), ""
                ),
                "nit_receptor": as_str(
                    interpreted.get("nit_receptor") or receptor.get("nit"), ""
                ),
                # total = total_devengado (gross salary expense — use for DR 5105xx)
                "total": str(parsed_total),
                # explicit fields so the LLM does not re-derive from employee items
                "total_devengado": str(parsed_total),
                "total_neto_pagar": str(raw_neto) if raw_neto is not None else None,
                "total_deducciones": (
                    str(raw_deducciones) if raw_deducciones is not None else None
                ),
                "concepto": concepto,
                "descripcion": concepto,
                # Strip neto_pagar from employee rows so the LLM cannot sum
                # them and use the result as the salary expense debit instead
                # of total_devengado (gross).
                "items": sanitize_for_json(
                    [
                        {k: v for k, v in (e or {}).items() if k != "neto_pagar"}
                        for e in (interpreted.get("empleados") or [])
                        if isinstance(e, dict)
                    ]
                ),
            }
        ]

    if doc_type == "planilla_seguridad_social":
        empresa = interpreted.get("empresa") or {}
        periodo = as_str(interpreted.get("periodo"), "")
        numero_planilla = as_str(interpreted.get("numero_planilla"), "")

        total_salud = safe_decimal(interpreted.get("total_salud")) or Decimal("0")
        total_pension = safe_decimal(interpreted.get("total_pension")) or Decimal("0")
        total_arl = safe_decimal(interpreted.get("total_arl")) or Decimal("0")
        total_caja = safe_decimal(interpreted.get("total_caja")) or Decimal("0")
        total_parafiscales = safe_decimal(
            interpreted.get("total_parafiscales")
        ) or Decimal("0")

        total_a_pagar = safe_decimal(interpreted.get("total_a_pagar"))
        if total_a_pagar is None or total_a_pagar == 0:
            total_a_pagar = (
                total_salud
                + total_pension
                + total_arl
                + total_caja
                + total_parafiscales
            )

        concepto = "Planilla seguridad social"
        if numero_planilla:
            concepto = f"{concepto} #{numero_planilla}"
        if periodo:
            concepto = f"{concepto} ({periodo})"

        return [
            {
                "fecha": interpreted.get("fecha") or interpreted.get("periodo"),
                "nit_emisor": as_str(
                    empresa.get("nit") or interpreted.get("nit_emisor"), ""
                ),
                "nit_receptor": as_str(
                    interpreted.get("nit_receptor") or receptor.get("nit"), ""
                ),
                "total": str(total_a_pagar),
                "total_salud": str(total_salud),
                "total_pension": str(total_pension),
                "total_arl": str(total_arl),
                "total_caja": str(total_caja),
                "total_parafiscales": str(total_parafiscales),
                "total_a_pagar": str(total_a_pagar),
                "concepto": concepto,
                "descripcion": concepto,
                "items": sanitize_for_json(
                    [
                        {
                            "numero_planilla": interpreted.get("numero_planilla"),
                            "periodo": periodo,
                            "total_salud": str(total_salud),
                            "total_pension": str(total_pension),
                            "total_arl": str(total_arl),
                            "total_caja": str(total_caja),
                            "total_parafiscales": str(total_parafiscales),
                        }
                    ]
                ),
            }
        ]

    if doc_type == "liquidacion_cesantias":
        empresa = interpreted.get("empresa") or {}
        fecha = (
            interpreted.get("fecha_pago")
            or interpreted.get("fecha_liquidacion")
            or interpreted.get("fecha")
        )

        # Prefer explicit consolidated totals exposed by the extractor.
        raw_total_cesantias = (
            interpreted.get("total_cesantias_liquidadas")
            or interpreted.get("total_cesantias")
            or interpreted.get("total")
        )
        raw_total_intereses = interpreted.get("total_intereses_cesantias")
        raw_total_prima = interpreted.get("total_prima_servicios")
        raw_total_vacaciones = interpreted.get("total_vacaciones")
        raw_total_retenciones = interpreted.get("total_retenciones")
        raw_total_neto = interpreted.get("total_neto_pagar")

        parsed_total = safe_decimal(raw_total_cesantias)
        if parsed_total is None or parsed_total == Decimal("0"):
            empleados = interpreted.get("empleados") or []
            if isinstance(empleados, list):
                parsed_total = sum(
                    [
                        safe_decimal((e or {}).get("valor_cesantias"))
                        or safe_decimal((e or {}).get("cesantias_liquidadas"))
                        or Decimal("0")
                        for e in empleados
                        if isinstance(e, dict)
                    ],
                    Decimal("0"),
                )
            else:
                parsed_total = Decimal("0")

        concepto = "Liquidacion cesantias"
        numero = as_str(
            interpreted.get("numero_documento") or interpreted.get("consecutivo"), ""
        ).strip()
        if numero:
            concepto = f"Liquidacion cesantias {numero}"

        empleados = interpreted.get("empleados") or []
        items = sanitize_for_json(empleados)

        tx = {
            "fecha": fecha,
            "nit_emisor": as_str(
                empresa.get("nit") or interpreted.get("nit_emisor"), ""
            ),
            "nit_receptor": as_str(
                interpreted.get("nit_receptor") or empresa.get("nit"), ""
            ),
            "total": str(parsed_total),
            "total_cesantias_liquidadas": str(
                safe_decimal(raw_total_cesantias) or parsed_total
            ),
            "total_intereses_cesantias": (
                str(safe_decimal(raw_total_intereses))
                if raw_total_intereses is not None
                and safe_decimal(raw_total_intereses) is not None
                else None
            ),
            "total_prima_servicios": (
                str(safe_decimal(raw_total_prima))
                if raw_total_prima is not None
                and safe_decimal(raw_total_prima) is not None
                else None
            ),
            "total_vacaciones": (
                str(safe_decimal(raw_total_vacaciones))
                if raw_total_vacaciones is not None
                and safe_decimal(raw_total_vacaciones) is not None
                else None
            ),
            "total_retenciones": (
                str(safe_decimal(raw_total_retenciones))
                if raw_total_retenciones is not None
                and safe_decimal(raw_total_retenciones) is not None
                else None
            ),
            "total_neto_pagar": (
                str(safe_decimal(raw_total_neto))
                if raw_total_neto is not None
                and safe_decimal(raw_total_neto) is not None
                else None
            ),
            "concepto": concepto,
            "descripcion": concepto,
            "items": items,
        }

        # Preserve pre-armed asiento table when present
        asientos_documento = interpreted.get("asientos_documento")
        if isinstance(asientos_documento, list) and asientos_documento:
            tx["asientos_documento"] = sanitize_for_json(asientos_documento)

        return [tx]

    if doc_type == "recibo_pago_impuesto":
        fecha = interpreted.get("fecha_pago") or interpreted.get("fecha")
        nit_emisor = as_str(
            interpreted.get("nit_declarante") or interpreted.get("nit_emisor"), ""
        )
        nit_receptor = as_str(
            interpreted.get("nit_receptor") or receptor.get("nit"), ""
        )
        periodo_gravable = as_str(interpreted.get("periodo_gravable"), "")
        base_items = sanitize_for_json(
            [
                {
                    "numero_recibo": interpreted.get("numero_recibo"),
                    "entidad_fiscal": interpreted.get("entidad_fiscal"),
                    "banco": interpreted.get("banco"),
                    "referencia_pago": interpreted.get("referencia_pago"),
                }
            ]
        )

        conceptos = interpreted.get("conceptos") or []
        if isinstance(conceptos, list) and len(conceptos) > 0:
            # One transaction per concepto line from Form 490 detail table.
            txs = []
            for c in conceptos:
                if not isinstance(c, dict):
                    continue
                raw_val = c.get("total") or c.get("valor_impuesto")
                valor = safe_decimal(raw_val) or Decimal("0")
                if valor == Decimal("0"):
                    continue
                codigo = as_str(c.get("codigo_concepto"), "")
                concepto_label = (
                    f"Pago impuesto concepto {codigo}" if codigo else "Pago de impuesto"
                )
                if periodo_gravable:
                    concepto_label = f"{concepto_label} ({periodo_gravable})"
                txs.append(
                    {
                        "fecha": fecha,
                        "nit_emisor": nit_emisor,
                        "nit_receptor": nit_receptor,
                        "total": str(valor),
                        "concepto": concepto_label,
                        "descripcion": concepto_label,
                        "items": sanitize_for_json(
                            [
                                {
                                    **base_items[0],
                                    "codigo_concepto": codigo,
                                    "numero_declaracion": c.get("numero_declaracion"),
                                    "numero_documento_origen": c.get(
                                        "numero_documento_origen"
                                    ),
                                    "valor_impuesto": c.get("valor_impuesto"),
                                    "valor_intereses": c.get("valor_intereses"),
                                    "valor_sancion": c.get("valor_sancion"),
                                }
                            ]
                        ),
                    }
                )
            if txs:
                return txs

        # Fallback: single transaction for total when no concepto breakdown available.
        raw_total = (
            interpreted.get("total_pagado")
            or interpreted.get("valor_principal")
            or interpreted.get("total")
        )
        parsed_total = safe_decimal(raw_total) or Decimal("0")
        tipo_impuesto = as_str(interpreted.get("tipo_impuesto"), "")
        concepto = "Pago de impuesto"
        if tipo_impuesto:
            concepto = f"Pago de impuesto {tipo_impuesto}"
        if periodo_gravable:
            concepto = f"{concepto} ({periodo_gravable})"

        return [
            {
                "fecha": fecha,
                "nit_emisor": nit_emisor,
                "nit_receptor": nit_receptor,
                "total": str(parsed_total),
                "concepto": concepto,
                "descripcion": concepto,
                "items": sanitize_for_json(
                    [
                        {
                            **base_items[0],
                            "valor_principal": interpreted.get("valor_principal"),
                            "sanciones": interpreted.get("sanciones"),
                            "intereses": interpreted.get("intereses"),
                            "total_pagado": interpreted.get("total_pagado"),
                        }
                    ]
                ),
            }
        ]

    if doc_type == "recibo_caja":
        recibido_de = interpreted.get("recibido_de") or {}
        numero_recibo = as_str(interpreted.get("numero_recibo"), "").strip()
        raw_total = interpreted.get("valor") or interpreted.get("total")
        parsed_total = safe_decimal(raw_total) or Decimal("0")

        base_concepto = as_str(interpreted.get("concepto"), "").strip()
        if not base_concepto:
            base_concepto = "Recibo de caja"
        if numero_recibo:
            concepto = f"Recibo de caja {numero_recibo}"
        else:
            concepto = base_concepto

        referencia_factura = as_str(interpreted.get("referencia_factura"), "").strip()
        if referencia_factura:
            concepto = f"{concepto} (Fact. {referencia_factura})"

        tipo_recibo = as_str(interpreted.get("tipo_recibo"), "").strip()

        return [
            {
                "fecha": interpreted.get("fecha"),
                "nit_emisor": as_str(
                    recibido_de.get("nit") or interpreted.get("nit_emisor"), ""
                ),
                "nit_receptor": "",
                "total": str(parsed_total),
                "concepto": concepto,
                "descripcion": concepto,
                "tipo_recibo": tipo_recibo,
                "referencia_factura": referencia_factura,
                "items": sanitize_for_json(
                    [
                        {
                            "numero_recibo": interpreted.get("numero_recibo"),
                            "recibido_de": sanitize_for_json(recibido_de),
                            "forma_pago": interpreted.get("forma_pago"),
                            "banco": interpreted.get("banco"),
                            "numero_cheque": interpreted.get("numero_cheque"),
                            "elaborado_por": interpreted.get("elaborado_por"),
                        }
                    ]
                ),
            }
        ]

    if doc_type == "cuenta_cobro":
        prestador = interpreted.get("prestador") or {}
        contratante = interpreted.get("contratante") or {}
        raw_total = interpreted.get("valor") or interpreted.get("valor_neto")
        parsed_total = safe_decimal(raw_total) or Decimal("0")
        concepto = as_str(interpreted.get("concepto"), "").strip() or "Cuenta de cobro"
        numero = as_str(interpreted.get("numero"), "").strip()
        if numero:
            concepto = f"Cuenta de cobro {numero} - {concepto}"

        # Cuenta de cobro is emitted by a non-IVA-responsible natural person —
        # total_iva = 0 by definition. Make it explicit so tributario does not
        # back-compute IVA from the gross total.
        totales_out = {
            "subtotal": str(parsed_total),
            "total_iva": "0",
            "total": str(parsed_total),
        }

        info_adicional = interpreted.get("informacion_adicional") or {}
        if not isinstance(info_adicional, dict):
            info_adicional = {}

        retenciones_extracted = interpreted.get("retenciones") or []
        if not isinstance(retenciones_extracted, list):
            retenciones_extracted = []

        return [
            {
                "fecha": interpreted.get("fecha"),
                "nit_emisor": as_str(
                    prestador.get("nit")
                    or prestador.get("cedula")
                    or interpreted.get("nit_emisor"),
                    "",
                ),
                "nit_receptor": as_str(
                    contratante.get("nit") or interpreted.get("nit_receptor"), ""
                ),
                "total": str(parsed_total),
                "concepto": concepto,
                "descripcion": concepto,
                "totales": totales_out,
                "retenciones_aplicadas": sanitize_for_json(retenciones_extracted),
                "informacion_adicional": sanitize_for_json(info_adicional),
                "items": [],
            }
        ]

    if doc_type in ("anexo_iva", "declaracion_iva"):
        nit_emisor = as_str(
            interpreted.get("nit_declarante") or interpreted.get("nit_emisor"), ""
        )
        # Prefer explicit periodo string; fall back to derived period-end date.
        periodo_str = as_str(interpreted.get("periodo"), "").strip()
        anio = interpreted.get("anio")
        periodicidad = as_str(interpreted.get("periodicidad"), "").lower().strip()
        periodo_numero = interpreted.get("periodo_numero")

        fecha = interpreted.get("fecha_presentacion") or interpreted.get("fecha")
        if not fecha and anio and periodo_numero:
            fecha = _derive_period_end(int(anio), periodicidad, int(periodo_numero))

        if doc_type == "anexo_iva":
            raw_total = (
                interpreted.get("saldo_a_pagar")
                or interpreted.get("saldo_a_favor")
                or interpreted.get("total_iva_generado")
            )
            parsed_total = safe_decimal(raw_total) or Decimal("0")

            concepto = "Anexo IVA"
            if periodo_str:
                concepto = f"Anexo IVA {periodo_str}"
            elif anio and periodo_numero:
                concepto = f"Anexo IVA periodo {periodo_numero} / {anio}"

            return [
                {
                    "fecha": fecha,
                    "nit_emisor": nit_emisor,
                    "nit_receptor": "",
                    "total": str(parsed_total),
                    "concepto": concepto,
                    "descripcion": concepto,
                    "total_iva_generado": str(
                        safe_decimal(interpreted.get("total_iva_generado"))
                        or Decimal("0")
                    ),
                    "total_iva_descontable": str(
                        safe_decimal(interpreted.get("total_iva_descontable"))
                        or Decimal("0")
                    ),
                    "saldo_a_pagar": str(
                        safe_decimal(interpreted.get("saldo_a_pagar")) or Decimal("0")
                    ),
                    "saldo_a_favor": str(
                        safe_decimal(interpreted.get("saldo_a_favor")) or Decimal("0")
                    ),
                    "retenciones_iva_practicadas": str(
                        safe_decimal(interpreted.get("retenciones_iva_practicadas"))
                        or Decimal("0")
                    ),
                    "retenciones_iva_que_le_practicaron": str(
                        safe_decimal(
                            interpreted.get("retenciones_iva_que_le_practicaron")
                        )
                        or Decimal("0")
                    ),
                    "items": sanitize_for_json(
                        [
                            {
                                "iva_generado": interpreted.get("iva_generado"),
                                "iva_descontable": interpreted.get("iva_descontable"),
                                "periodicidad": periodicidad,
                                "periodo_numero": periodo_numero,
                                "anio": anio,
                            }
                        ]
                    ),
                }
            ]

        # declaracion_iva — TaxDeclarationContent
        raw_total = interpreted.get("total_a_pagar") or interpreted.get("saldo_a_favor")
        parsed_total = safe_decimal(raw_total) or Decimal("0")

        concepto = "Declaracion IVA"
        if periodo_str:
            concepto = f"Declaracion IVA {periodo_str}"
        elif anio and periodo_numero:
            concepto = f"Declaracion IVA periodo {periodo_numero} / {anio}"

        return [
            {
                "fecha": fecha,
                "nit_emisor": nit_emisor,
                "nit_receptor": "",
                "total": str(parsed_total),
                "concepto": concepto,
                "descripcion": concepto,
                "total_a_pagar": str(
                    safe_decimal(interpreted.get("total_a_pagar")) or Decimal("0")
                ),
                "saldo_a_favor": str(
                    safe_decimal(interpreted.get("saldo_a_favor")) or Decimal("0")
                ),
                "items": sanitize_for_json(
                    [
                        {
                            "formulario": interpreted.get("formulario"),
                            "renglones": interpreted.get("renglones"),
                            "impuestos_descontables": interpreted.get(
                                "impuestos_descontables"
                            ),
                            "periodicidad": periodicidad,
                            "periodo_numero": periodo_numero,
                            "anio": anio,
                        }
                    ]
                ),
            }
        ]

    if doc_type == "autorretencion_ica":
        nit_emisor = as_str(interpreted.get("nit_declarante"), "")
        municipio = as_str(interpreted.get("municipio"), "").strip()
        departamento = as_str(interpreted.get("departamento"), "").strip()
        anio = interpreted.get("anio")
        periodicidad = as_str(interpreted.get("periodicidad"), "").lower().strip()
        periodo_numero = interpreted.get("periodo_numero")

        fecha = interpreted.get("fecha_presentacion")
        if not fecha and anio and periodicidad and periodo_numero:
            fecha = _derive_period_end(int(anio), periodicidad, int(periodo_numero))

        total_a_pagar = safe_decimal(interpreted.get("total_a_pagar"))
        total_autorretenciones = safe_decimal(interpreted.get("total_autorretenciones"))
        if total_a_pagar and total_a_pagar > Decimal("0"):
            total = total_a_pagar
        elif total_autorretenciones and total_autorretenciones > Decimal("0"):
            total = total_autorretenciones
        else:
            total = Decimal("0")

        if municipio and periodo_numero is not None and anio:
            concepto = f"Autorretencion ICA {municipio} periodo {periodo_numero}/{anio}"
        else:
            concepto = "Autorretencion ICA"

        detalle = interpreted.get("detalle_autorretenciones") or []
        if not isinstance(detalle, list):
            detalle = []

        return [
            {
                "fecha": fecha,
                "nit_emisor": nit_emisor,
                "nit_receptor": "",
                "total": str(total),
                "concepto": concepto,
                "descripcion": concepto,
                "total_autorretenciones": str(
                    total_autorretenciones
                    if total_autorretenciones is not None
                    else Decimal("0")
                ),
                "sanciones": str(
                    safe_decimal(interpreted.get("sanciones")) or Decimal("0")
                ),
                "intereses_mora": str(
                    safe_decimal(interpreted.get("intereses_mora")) or Decimal("0")
                ),
                "municipio": municipio,
                "departamento": departamento,
                "items": sanitize_for_json(detalle),
            }
        ]

    if doc_type == "auxiliar_iva":
        entidad = interpreted.get("entidad") or {}
        if isinstance(entidad, dict):
            nit_emisor = as_str(entidad.get("nit"), "")
        else:
            nit_emisor = as_str(getattr(entidad, "nit", None), "")
        fecha = interpreted.get("periodo_fin")
        cuentas = interpreted.get("cuentas") or []

        if not isinstance(cuentas, list) or not cuentas:
            return [
                {
                    "fecha": fecha,
                    "nit_emisor": nit_emisor,
                    "nit_receptor": "",
                    "total": "0",
                    "concepto": "Auxiliar IVA",
                    "descripcion": "Auxiliar IVA",
                    "items": [],
                }
            ]

        txs = []
        for cuenta in cuentas:
            if not isinstance(cuenta, dict):
                continue
            codigo = as_str(cuenta.get("codigo_cuenta"), "")
            nombre = as_str(cuenta.get("nombre_cuenta"), "").strip()
            tipo_iva = as_str(cuenta.get("tipo_iva"), "")
            concepto = f"Auxiliar IVA {nombre}" if nombre else f"Auxiliar IVA {codigo}"

            saldo_final = safe_decimal(cuenta.get("saldo_final")) or Decimal("0")
            total_debitos = safe_decimal(cuenta.get("total_debitos")) or Decimal("0")
            total_creditos = safe_decimal(cuenta.get("total_creditos")) or Decimal("0")
            saldo_inicial = safe_decimal(cuenta.get("saldo_inicial")) or Decimal("0")

            if saldo_final > Decimal("0"):
                total = saldo_final
            else:
                total = max(total_debitos, total_creditos)

            movimientos = cuenta.get("movimientos") or []
            if not isinstance(movimientos, list):
                movimientos = []

            txs.append(
                {
                    "fecha": fecha,
                    "nit_emisor": nit_emisor,
                    "nit_receptor": "",
                    "total": str(total),
                    "concepto": concepto,
                    "descripcion": concepto,
                    "codigo_cuenta": codigo,
                    "tipo_iva": tipo_iva,
                    "total_debitos": str(total_debitos),
                    "total_creditos": str(total_creditos),
                    "saldo_inicial": str(saldo_inicial),
                    "saldo_final": str(saldo_final),
                    "items": sanitize_for_json(movimientos[:20]),
                }
            )

        if txs:
            return txs

        return [
            {
                "fecha": fecha,
                "nit_emisor": nit_emisor,
                "nit_receptor": "",
                "total": "0",
                "concepto": "Auxiliar IVA",
                "descripcion": "Auxiliar IVA",
                "items": [],
            }
        ]

    if doc_type == "declaracion_ica":
        nit_emisor = as_str(interpreted.get("nit_declarante"), "")
        municipio = as_str(interpreted.get("municipio"), "").strip()
        departamento = as_str(interpreted.get("departamento"), "").strip()
        anio = interpreted.get("anio")
        periodicidad = as_str(interpreted.get("periodicidad"), "").lower().strip()
        periodo_numero = interpreted.get("periodo_numero")

        fecha = interpreted.get("fecha_presentacion")
        if not fecha and anio and periodicidad and periodo_numero:
            fecha = _derive_period_end(int(anio), periodicidad, int(periodo_numero))

        liq = interpreted.get("liquidacion") or {}
        total_a_pagar = safe_decimal(liq.get("total_a_pagar"))
        impuesto_ica = safe_decimal(liq.get("impuesto_ica"))
        saldo_a_favor = safe_decimal(liq.get("saldo_a_favor"))

        if total_a_pagar and total_a_pagar > Decimal("0"):
            total = total_a_pagar
        elif impuesto_ica and impuesto_ica > Decimal("0"):
            total = impuesto_ica
        elif saldo_a_favor and saldo_a_favor > Decimal("0"):
            total = saldo_a_favor
        else:
            total = Decimal("0")

        if municipio and periodo_numero is not None and anio:
            concepto = f"Declaracion ICA {municipio} periodo {periodo_numero}/{anio}"
        else:
            concepto = "Declaracion ICA"

        actividades = interpreted.get("actividades_economicas") or []
        if not isinstance(actividades, list):
            actividades = []

        return [
            {
                "fecha": fecha,
                "nit_emisor": nit_emisor,
                "nit_receptor": "",
                "total": str(total),
                "concepto": concepto,
                "descripcion": concepto,
                "total_a_pagar": str(
                    total_a_pagar if total_a_pagar is not None else Decimal("0")
                ),
                "impuesto_ica": str(
                    impuesto_ica if impuesto_ica is not None else Decimal("0")
                ),
                "saldo_a_favor": str(
                    saldo_a_favor if saldo_a_favor is not None else Decimal("0")
                ),
                "municipio": municipio,
                "departamento": departamento,
                "ingresos_brutos": str(
                    safe_decimal(interpreted.get("ingresos_brutos")) or Decimal("0")
                ),
                "total_ingresos_gravables": str(
                    safe_decimal(interpreted.get("total_ingresos_gravables"))
                    or Decimal("0")
                ),
                "items": sanitize_for_json(actividades),
            }
        ]

    # --- Generic fallback mapping ---
    raw_total = (
        # Invoice-like schemas
        totales.get("total_a_pagar")
        or totales.get("total")
        # Voucher-like schemas
        or interpreted.get("valor_neto")
        or interpreted.get("valor_bruto")
        # Generic fallbacks
        or interpreted.get("total")
        or interpreted.get("valor_total")
        or interpreted.get("valor")
        or interpreted.get("monto")
    )
    parsed_total = safe_decimal(raw_total)
    if parsed_total is None or parsed_total == Decimal("0"):
        inferred_total = infer_total_from_items(items_payload)
        if inferred_total is not None and inferred_total > Decimal("0"):
            parsed_total = inferred_total

    # Derive a meaningful descripcion. Order: explicit fields → notas →
    # concat of first item descriptions → consecutivo-based fallback. Without
    # this FVs persist with empty `descripcion` and the UI shows "—".
    derived_concepto = as_str(
        interpreted.get("descripcion_general")
        or interpreted.get("concepto")
        or interpreted.get("notas"),
        "",
    ).strip()
    if not derived_concepto and isinstance(items_payload, list) and items_payload:
        item_descs: list[str] = []
        for item in items_payload[:3]:
            if isinstance(item, dict):
                desc = as_str(
                    item.get("descripcion") or item.get("concepto"), ""
                ).strip()
                if desc:
                    item_descs.append(desc)
        if item_descs:
            derived_concepto = " · ".join(item_descs)[:200]
    if not derived_concepto:
        consecutivo = as_str(
            interpreted.get("consecutivo") or interpreted.get("numero"), ""
        ).strip()
        emisor_nit = as_str(emisor.get("nit") or interpreted.get("nit_emisor"), "")
        tipo = as_str(doc_type or interpreted.get("tipo_documento", ""), "")
        if consecutivo:
            derived_concepto = (
                f"{tipo} {consecutivo}".strip() if tipo else f"Doc {consecutivo}"
            )
        elif emisor_nit:
            derived_concepto = f"{tipo} - NIT {emisor_nit}".strip(" -")
        else:
            derived_concepto = tipo

    tx_data = {
        "fecha": (
            interpreted.get("fecha_emision")
            or interpreted.get("fecha_registro")
            or interpreted.get("fecha")
        ),
        "nit_emisor": as_str(emisor.get("nit") or interpreted.get("nit_emisor"), ""),
        "nit_receptor": as_str(
            receptor.get("nit") or interpreted.get("nit_receptor"), ""
        ),
        "total": str(parsed_total if parsed_total is not None else Decimal("0")),
        "concepto": derived_concepto,
        "descripcion": derived_concepto,
        "items": sanitize_for_json(items_payload),
        "totales": sanitize_for_json(totales) if totales else None,
        "retenciones_aplicadas": sanitize_for_json(
            interpreted.get("retenciones_aplicadas") or []
        ),
    }

    # Pre-armed journal entry table (CE, RC, Nómina, manual journal). The
    # extractor populates ``asientos_documento`` when the source doc prints a
    # CODIGO CUENTA + DEBITO + CREDITO table. Persist it verbatim so the
    # downstream contador/tributario passthrough can pick it up.
    asientos_documento = interpreted.get("asientos_documento")
    if isinstance(asientos_documento, list) and asientos_documento:
        tx_data["asientos_documento"] = sanitize_for_json(asientos_documento)

    return [tx_data]
