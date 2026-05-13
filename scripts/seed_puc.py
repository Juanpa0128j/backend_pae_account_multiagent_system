"""
Seed script for populating the CuentaPUC table with core Colombian PUC accounts.

Run: python scripts/seed_puc.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.database import CuentaPUC, NaturalezaCuenta
from app.core.logger import get_logger

logger = get_logger(__name__)

# Core PUC accounts organized by class
PUC_ACCOUNTS = [
    # ── Clase 1: ACTIVOS (Naturaleza: Débito) ──
    {
        "codigo": "1105",
        "nombre": "Caja",
        "clase": 1,
        "grupo": "11",
        "naturaleza": "debito",
        "descripcion": "Dinero en efectivo y cheques recibidos pendientes de consignar",
    },
    {
        "codigo": "110505",
        "nombre": "Caja General",
        "clase": 1,
        "grupo": "11",
        "cuenta": "1105",
        "naturaleza": "debito",
        "descripcion": "Caja general de la empresa",
    },
    {
        "codigo": "1110",
        "nombre": "Bancos",
        "clase": 1,
        "grupo": "11",
        "naturaleza": "debito",
        "descripcion": "Saldo en cuentas bancarias (corrientes y de ahorro)",
    },
    {
        "codigo": "111005",
        "nombre": "Bancos Nacionales",
        "clase": 1,
        "grupo": "11",
        "cuenta": "1110",
        "naturaleza": "debito",
        "descripcion": "Cuentas en bancos nacionales",
    },
    {
        "codigo": "1305",
        "nombre": "Clientes",
        "clase": 1,
        "grupo": "13",
        "naturaleza": "debito",
        "descripcion": "Cuentas por cobrar a clientes por ventas a crédito",
    },
    {
        "codigo": "130505",
        "nombre": "Clientes Nacionales",
        "clase": 1,
        "grupo": "13",
        "cuenta": "1305",
        "naturaleza": "debito",
        "descripcion": "Clientes nacionales",
    },
    {
        "codigo": "1380",
        "nombre": "Deudores Varios",
        "clase": 1,
        "grupo": "13",
        "naturaleza": "debito",
        "descripcion": "Otros deudores",
    },
    {
        "codigo": "1435",
        "nombre": "Mercancías no fabricadas por la empresa",
        "clase": 1,
        "grupo": "14",
        "naturaleza": "debito",
        "descripcion": "Inventarios de mercancías para venta",
    },
    {
        "codigo": "1524",
        "nombre": "Equipo de Oficina",
        "clase": 1,
        "grupo": "15",
        "naturaleza": "debito",
        "descripcion": "Muebles y equipos de oficina",
    },
    {
        "codigo": "1528",
        "nombre": "Equipo de Computación",
        "clase": 1,
        "grupo": "15",
        "naturaleza": "debito",
        "descripcion": "Equipos de sistemas y comunicaciones",
    },
    {
        "codigo": "1592",
        "nombre": "Depreciación Acumulada",
        "clase": 1,
        "grupo": "15",
        "naturaleza": "credito",
        "descripcion": "Depreciación acumulada de propiedad, planta y equipo",
    },
    # ── Clase 2: PASIVOS (Naturaleza: Crédito) ──
    {
        "codigo": "2105",
        "nombre": "Bancos Nacionales",
        "clase": 2,
        "grupo": "21",
        "naturaleza": "credito",
        "descripcion": "Obligaciones con bancos nacionales (préstamos y créditos de entidades financieras)",
    },
    {
        "codigo": "2205",
        "nombre": "Proveedores Nacionales",
        "clase": 2,
        "grupo": "22",
        "naturaleza": "credito",
        "descripcion": "Cuentas por pagar a proveedores nacionales",
    },
    {
        "codigo": "220505",
        "nombre": "Proveedores Nacionales",
        "clase": 2,
        "grupo": "22",
        "cuenta": "2205",
        "naturaleza": "credito",
        "descripcion": "Proveedores nacionales de bienes y servicios",
    },
    {
        "codigo": "2335",
        "nombre": "Costos y Gastos por Pagar",
        "clase": 2,
        "grupo": "23",
        "naturaleza": "credito",
        "descripcion": "Obligaciones por costos y gastos causados no pagados",
    },
    {
        "codigo": "2365",
        "nombre": "Retención en la Fuente",
        "clase": 2,
        "grupo": "23",
        "naturaleza": "credito",
        "descripcion": "Retenciones en la fuente practicadas a terceros pendientes de declarar y pagar",
    },
    {
        "codigo": "236505",
        "nombre": "Retención en la Fuente - Salarios",
        "clase": 2,
        "grupo": "23",
        "cuenta": "2365",
        "naturaleza": "credito",
        "descripcion": "Retefuente sobre salarios y pagos laborales",
    },
    {
        "codigo": "236510",
        "nombre": "Retención en la Fuente - Honorarios",
        "clase": 2,
        "grupo": "23",
        "cuenta": "2365",
        "naturaleza": "credito",
        "descripcion": "Retefuente sobre honorarios",
    },
    {
        "codigo": "236515",
        "nombre": "Retención en la Fuente - Servicios",
        "clase": 2,
        "grupo": "23",
        "cuenta": "2365",
        "naturaleza": "credito",
        "descripcion": "Retefuente sobre servicios",
    },
    {
        "codigo": "236540",
        "nombre": "Retención de Industria y Comercio (ReteICA)",
        "clase": 2,
        "grupo": "23",
        "cuenta": "2365",
        "naturaleza": "credito",
        "descripcion": "ReteICA retenido pendiente de declarar y pagar",
    },
    {
        "codigo": "2404",
        "nombre": "De Renta y Complementarios",
        "clase": 2,
        "grupo": "24",
        "naturaleza": "credito",
        "descripcion": "Impuesto de renta y complementarios por pagar",
    },
    {
        "codigo": "2408",
        "nombre": "Impuesto sobre las Ventas por Pagar",
        "clase": 2,
        "grupo": "24",
        "naturaleza": "credito",
        "descripcion": "IVA generado en ventas pendiente de declarar y pagar",
    },
    {
        "codigo": "240802",
        "nombre": "IVA Descontable",
        "clase": 2,
        "grupo": "24",
        "cuenta": "2408",
        "naturaleza": "debito",
        "descripcion": "IVA pagado en compras - descontable",
    },
    {
        "codigo": "240805",
        "nombre": "IVA Generado",
        "clase": 2,
        "grupo": "24",
        "cuenta": "2408",
        "naturaleza": "credito",
        "descripcion": "IVA facturado en ventas",
    },
    {
        "codigo": "240815",
        "nombre": "Retención en la Fuente por Pagar (LEGACY)",
        "clase": 2,
        "grupo": "24",
        "cuenta": "2408",
        "naturaleza": "credito",
        "descripcion": "Cuenta legacy 240815 conservada para compatibilidad con flujos existentes de retefuente",
    },
    {
        "codigo": "2505",
        "nombre": "Salarios por Pagar",
        "clase": 2,
        "grupo": "25",
        "naturaleza": "credito",
        "descripcion": "Obligaciones laborales por salarios",
    },
    # ── Clase 3: PATRIMONIO (Naturaleza: Crédito) ──
    {
        "codigo": "3105",
        "nombre": "Capital Suscrito y Pagado",
        "clase": 3,
        "grupo": "31",
        "naturaleza": "credito",
        "descripcion": "Capital aportado por los socios",
    },
    {
        "codigo": "3115",
        "nombre": "Aportes Sociales",
        "clase": 3,
        "grupo": "31",
        "naturaleza": "credito",
        "descripcion": "Aportes de socios en SAS, Ltda, etc.",
    },
    {
        "codigo": "3305",
        "nombre": "Reservas Obligatorias",
        "clase": 3,
        "grupo": "33",
        "naturaleza": "credito",
        "descripcion": "Reserva legal (10% de utilidades)",
    },
    {
        "codigo": "3605",
        "nombre": "Utilidad del Ejercicio",
        "clase": 3,
        "grupo": "36",
        "naturaleza": "credito",
        "descripcion": "Resultado neto del período actual",
    },
    {
        "codigo": "3705",
        "nombre": "Utilidades Acumuladas",
        "clase": 3,
        "grupo": "37",
        "naturaleza": "credito",
        "descripcion": "Utilidades de ejercicios anteriores no distribuidas",
    },
    # ── Clase 4: INGRESOS (Naturaleza: Crédito) ──
    {
        "codigo": "4135",
        "nombre": "Comercio al por Mayor y Menor",
        "clase": 4,
        "grupo": "41",
        "naturaleza": "credito",
        "descripcion": "Ingresos por venta de mercancías",
    },
    {
        "codigo": "4170",
        "nombre": "Servicios",
        "clase": 4,
        "grupo": "41",
        "naturaleza": "credito",
        "descripcion": "Ingresos por prestación de servicios",
    },
    {
        "codigo": "4175",
        "nombre": "Devoluciones en Ventas (DB)",
        "clase": 4,
        "grupo": "41",
        "naturaleza": "debito",
        "descripcion": "Devoluciones y descuentos en ventas",
    },
    {
        "codigo": "4210",
        "nombre": "Financieros",
        "clase": 4,
        "grupo": "42",
        "naturaleza": "credito",
        "descripcion": "Ingresos por rendimientos financieros",
    },
    # ── Clase 5: GASTOS (Naturaleza: Débito) ──
    {
        "codigo": "5105",
        "nombre": "Gastos de Personal",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Salarios, prestaciones, seguridad social",
    },
    {
        "codigo": "5110",
        "nombre": "Honorarios",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Pagos por servicios profesionales",
    },
    {
        "codigo": "5115",
        "nombre": "Impuestos",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Impuestos asumidos como gasto",
    },
    {
        "codigo": "5120",
        "nombre": "Arrendamientos",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Gastos por arriendo de inmuebles y equipos",
    },
    {
        "codigo": "5130",
        "nombre": "Seguros",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Primas de seguros",
    },
    {
        "codigo": "5135",
        "nombre": "Servicios Públicos",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Agua, energía, teléfono, internet",
    },
    {
        "codigo": "5140",
        "nombre": "Gastos Legales",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Gastos de notaría, registro, trámites legales",
    },
    {
        "codigo": "5195",
        "nombre": "Gastos Diversos",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Otros gastos no clasificados en cuentas específicas",
    },
    {
        "codigo": "5199",
        "nombre": "Provisiones",
        "clase": 5,
        "grupo": "51",
        "naturaleza": "debito",
        "descripcion": "Provisiones sobre cuentas por cobrar y otros activos",
    },
    # ── 6-digit gasto subaccounts (admon/operacionales) ──
    {
        "codigo": "510505",
        "nombre": "Sueldos",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Sueldos básicos del personal administrativo",
    },
    {
        "codigo": "510510",
        "nombre": "Cesantías",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Cesantías consolidadas del personal",
    },
    {
        "codigo": "510515",
        "nombre": "Intereses sobre Cesantías",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Intereses anuales sobre cesantías (12% anual)",
    },
    {
        "codigo": "510518",
        "nombre": "Prima de Servicios",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Prima legal de servicios (junio y diciembre)",
    },
    {
        "codigo": "510521",
        "nombre": "Vacaciones",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Vacaciones causadas y compensadas",
    },
    {
        "codigo": "510527",
        "nombre": "Aportes a EPS",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Aportes empresariales a salud (EPS)",
    },
    {
        "codigo": "510530",
        "nombre": "Aportes a ARL",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Aportes empresariales a riesgos laborales (ARL)",
    },
    {
        "codigo": "510533",
        "nombre": "Aportes a Fondo de Pensiones",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5105",
        "naturaleza": "debito",
        "descripcion": "Aportes empresariales a pensión",
    },
    {
        "codigo": "511505",
        "nombre": "Honorarios",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5115",
        "naturaleza": "debito",
        "descripcion": "Honorarios profesionales (contabilidad, auditoría, consultoría)",
    },
    {
        "codigo": "511510",
        "nombre": "Comisiones",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5115",
        "naturaleza": "debito",
        "descripcion": "Comisiones a terceros por intermediación",
    },
    {
        "codigo": "511515",
        "nombre": "Servicios Jurídicos",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5115",
        "naturaleza": "debito",
        "descripcion": "Asesoría jurídica y representación legal",
    },
    {
        "codigo": "511525",
        "nombre": "Servicios Técnicos",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5115",
        "naturaleza": "debito",
        "descripcion": "Servicios técnicos especializados",
    },
    {
        "codigo": "511595",
        "nombre": "Otros Honorarios",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5115",
        "naturaleza": "debito",
        "descripcion": "Otros honorarios no clasificados",
    },
    {
        "codigo": "513025",
        "nombre": "Combustibles y Lubricantes",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5130",
        "naturaleza": "debito",
        "descripcion": "Combustibles, ACPM, gasolina, aceites",
    },
    {
        "codigo": "513540",
        "nombre": "Servicios Públicos",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5135",
        "naturaleza": "debito",
        "descripcion": "Energía, acueducto, gas, internet, telefonía",
    },
    {
        "codigo": "513550",
        "nombre": "Transporte, Fletes y Acarreos",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5135",
        "naturaleza": "debito",
        "descripcion": "Servicios de transporte y mensajería",
    },
    {
        "codigo": "514505",
        "nombre": "Mantenimiento y Reparaciones",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5140",
        "naturaleza": "debito",
        "descripcion": "Mantenimiento de equipos, edificios y vehículos",
    },
    {
        "codigo": "514515",
        "nombre": "Aseo y Limpieza",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5140",
        "naturaleza": "debito",
        "descripcion": "Servicios de aseo y limpieza de instalaciones",
    },
    {
        "codigo": "514525",
        "nombre": "Vigilancia",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5140",
        "naturaleza": "debito",
        "descripcion": "Servicios de vigilancia y seguridad",
    },
    {
        "codigo": "519520",
        "nombre": "Cuotas de Afiliación",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5195",
        "naturaleza": "debito",
        "descripcion": "Cuotas a gremios, cámaras y asociaciones",
    },
    {
        "codigo": "519525",
        "nombre": "Útiles, Papelería y Software",
        "clase": 5,
        "grupo": "51",
        "cuenta": "5195",
        "naturaleza": "debito",
        "descripcion": "Útiles de oficina, papelería, licencias de software, hosting",
    },
    {
        "codigo": "521505",
        "nombre": "ICA — Gasto de Ventas",
        "clase": 5,
        "grupo": "52",
        "naturaleza": "debito",
        "descripcion": "Impuesto de industria y comercio causado en gasto",
    },
    {
        "codigo": "523510",
        "nombre": "Publicidad y Mercadeo",
        "clase": 5,
        "grupo": "52",
        "naturaleza": "debito",
        "descripcion": "Publicidad, propaganda y promoción comercial",
    },
    {
        "codigo": "529505",
        "nombre": "GMF (4x1000)",
        "clase": 5,
        "grupo": "52",
        "naturaleza": "debito",
        "descripcion": "Gravamen a los movimientos financieros (4x1000)",
    },
    {
        "codigo": "5305",
        "nombre": "Gastos Financieros",
        "clase": 5,
        "grupo": "53",
        "naturaleza": "debito",
        "descripcion": "Intereses, comisiones bancarias, GMF",
    },
    {
        "codigo": "530520",
        "nombre": "Intereses",
        "clase": 5,
        "grupo": "53",
        "cuenta": "5305",
        "naturaleza": "debito",
        "descripcion": "Intereses sobre obligaciones financieras",
    },
    {
        "codigo": "530525",
        "nombre": "Comisiones Bancarias",
        "clase": 5,
        "grupo": "53",
        "cuenta": "5305",
        "naturaleza": "debito",
        "descripcion": "Comisiones por servicios financieros",
    },
    # ── Clase 6: COSTOS DE VENTA (Naturaleza: Débito) ──
    {
        "codigo": "6135",
        "nombre": "Costo de Venta - Comercio",
        "clase": 6,
        "grupo": "61",
        "naturaleza": "debito",
        "descripcion": "Costo de la mercancía vendida",
    },
    {
        "codigo": "6170",
        "nombre": "Costo de Venta - Servicios",
        "clase": 6,
        "grupo": "61",
        "naturaleza": "debito",
        "descripcion": "Costo de prestación de servicios",
    },
    # ── Clase 7: COSTOS DE PRODUCCIÓN (Naturaleza: Débito) ──
    {
        "codigo": "7",
        "nombre": "Costos de Producción",
        "clase": 7,
        "naturaleza": "debito",
        "descripcion": "Costos incurridos en el proceso de producción o fabricación",
    },
    {
        "codigo": "72",
        "nombre": "Costos Indirectos",
        "clase": 7,
        "grupo": "72",
        "naturaleza": "debito",
        "descripcion": "Costos indirectos de fabricación y producción",
    },
    {
        "codigo": "7205",
        "nombre": "Costos Indirectos de Fabricación",
        "clase": 7,
        "grupo": "72",
        "naturaleza": "debito",
        "descripcion": "Costos generales de producción no directamente asignables",
    },
    {
        "codigo": "720505",
        "nombre": "Materiales Indirectos",
        "clase": 7,
        "grupo": "72",
        "naturaleza": "debito",
        "descripcion": "Materiales y suministros de uso indirecto en producción",
    },
    {
        "codigo": "76",
        "nombre": "Ajustes por Inflación - Costos",
        "clase": 7,
        "grupo": "76",
        "naturaleza": "debito",
        "descripcion": "Ajustes de ejercicios anteriores y correcciones de costos de producción",
    },
    {
        "codigo": "7605",
        "nombre": "Ajustes de Costos de Producción",
        "clase": 7,
        "grupo": "76",
        "naturaleza": "debito",
        "descripcion": "Ajustes y correcciones sobre costos de producción de períodos anteriores",
    },
    {
        "codigo": "760505",
        "nombre": "Ajustes y Devoluciones de Costos de Producción",
        "clase": 7,
        "grupo": "76",
        "naturaleza": "debito",
        "descripcion": "Notas débito, devoluciones y ajustes relacionados con costos de producción",
    },
]


def seed_puc():
    """Insert PUC accounts into the database."""
    db = SessionLocal()
    try:
        inserted = 0
        skipped = 0

        for account_data in PUC_ACCOUNTS:
            existing = (
                db.query(CuentaPUC)
                .filter(CuentaPUC.codigo == account_data["codigo"])
                .first()
            )

            if existing:
                skipped += 1
                continue

            account = CuentaPUC(
                codigo=account_data["codigo"],
                nombre=account_data["nombre"],
                clase=account_data["clase"],
                grupo=account_data.get("grupo"),
                cuenta=account_data.get("cuenta"),
                subcuenta=account_data.get("subcuenta"),
                naturaleza=NaturalezaCuenta[account_data["naturaleza"].upper()],
                descripcion=account_data.get("descripcion"),
                activa=True,
            )
            db.add(account)
            inserted += 1

        db.commit()
        logger.info(
            f"PUC seed complete: {inserted} inserted, {skipped} skipped (already existed)"
        )
        print(f"✅ PUC seed complete: {inserted} inserted, {skipped} skipped")

    except Exception as e:
        db.rollback()
        logger.error(f"PUC seed failed: {e}")
        print(f"❌ PUC seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_puc()
