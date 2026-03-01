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
    {"codigo": "1105", "nombre": "Caja", "clase": 1, "grupo": "11", "naturaleza": "debito",
     "descripcion": "Dinero en efectivo y cheques recibidos pendientes de consignar"},
    {"codigo": "110505", "nombre": "Caja General", "clase": 1, "grupo": "11", "cuenta": "1105", "naturaleza": "debito",
     "descripcion": "Caja general de la empresa"},
    {"codigo": "1110", "nombre": "Bancos", "clase": 1, "grupo": "11", "naturaleza": "debito",
     "descripcion": "Saldo en cuentas bancarias (corrientes y de ahorro)"},
    {"codigo": "111005", "nombre": "Bancos Nacionales", "clase": 1, "grupo": "11", "cuenta": "1110", "naturaleza": "debito",
     "descripcion": "Cuentas en bancos nacionales"},
    {"codigo": "1305", "nombre": "Clientes", "clase": 1, "grupo": "13", "naturaleza": "debito",
     "descripcion": "Cuentas por cobrar a clientes por ventas a crédito"},
    {"codigo": "130505", "nombre": "Clientes Nacionales", "clase": 1, "grupo": "13", "cuenta": "1305", "naturaleza": "debito",
     "descripcion": "Clientes nacionales"},
    {"codigo": "1380", "nombre": "Deudores Varios", "clase": 1, "grupo": "13", "naturaleza": "debito",
     "descripcion": "Otros deudores"},
    {"codigo": "1435", "nombre": "Mercancías no fabricadas por la empresa", "clase": 1, "grupo": "14", "naturaleza": "debito",
     "descripcion": "Inventarios de mercancías para venta"},
    {"codigo": "1524", "nombre": "Equipo de Oficina", "clase": 1, "grupo": "15", "naturaleza": "debito",
     "descripcion": "Muebles y equipos de oficina"},
    {"codigo": "1528", "nombre": "Equipo de Computación", "clase": 1, "grupo": "15", "naturaleza": "debito",
     "descripcion": "Equipos de sistemas y comunicaciones"},
    {"codigo": "1592", "nombre": "Depreciación Acumulada", "clase": 1, "grupo": "15", "naturaleza": "credito",
     "descripcion": "Depreciación acumulada de propiedad, planta y equipo"},

    # ── Clase 2: PASIVOS (Naturaleza: Crédito) ──
    {"codigo": "2105", "nombre": "Bancos Nacionales", "clase": 2, "grupo": "21", "naturaleza": "credito",
     "descripcion": "Obligaciones con bancos nacionales (préstamos y créditos de entidades financieras)"},
    {"codigo": "2205", "nombre": "Proveedores Nacionales", "clase": 2, "grupo": "22", "naturaleza": "credito",
     "descripcion": "Cuentas por pagar a proveedores nacionales"},
    {"codigo": "220505", "nombre": "Proveedores Nacionales", "clase": 2, "grupo": "22", "cuenta": "2205", "naturaleza": "credito",
     "descripcion": "Proveedores nacionales de bienes y servicios"},
    {"codigo": "2335", "nombre": "Costos y Gastos por Pagar", "clase": 2, "grupo": "23", "naturaleza": "credito",
     "descripcion": "Obligaciones por costos y gastos causados no pagados"},
    {"codigo": "2365", "nombre": "Retención en la Fuente", "clase": 2, "grupo": "23", "naturaleza": "credito",
     "descripcion": "Retenciones en la fuente practicadas a terceros pendientes de declarar y pagar"},
    {"codigo": "236505", "nombre": "Retención en la Fuente - Salarios", "clase": 2, "grupo": "23", "cuenta": "2365", "naturaleza": "credito",
     "descripcion": "Retefuente sobre salarios y pagos laborales"},
    {"codigo": "236510", "nombre": "Retención en la Fuente - Honorarios", "clase": 2, "grupo": "23", "cuenta": "2365", "naturaleza": "credito",
     "descripcion": "Retefuente sobre honorarios"},
    {"codigo": "236515", "nombre": "Retención en la Fuente - Servicios", "clase": 2, "grupo": "23", "cuenta": "2365", "naturaleza": "credito",
     "descripcion": "Retefuente sobre servicios"},
    {"codigo": "236540", "nombre": "Retención de Industria y Comercio (ReteICA)", "clase": 2, "grupo": "23", "cuenta": "2365", "naturaleza": "credito",
     "descripcion": "ReteICA retenido pendiente de declarar y pagar"},
    {"codigo": "2404", "nombre": "De Renta y Complementarios", "clase": 2, "grupo": "24", "naturaleza": "credito",
     "descripcion": "Impuesto de renta y complementarios por pagar"},
    {"codigo": "2408", "nombre": "Impuesto sobre las Ventas por Pagar", "clase": 2, "grupo": "24", "naturaleza": "credito",
     "descripcion": "IVA generado en ventas pendiente de declarar y pagar"},
    {"codigo": "240802", "nombre": "IVA Descontable", "clase": 2, "grupo": "24", "cuenta": "2408", "naturaleza": "debito",
     "descripcion": "IVA pagado en compras - descontable"},
    {"codigo": "240805", "nombre": "IVA Generado", "clase": 2, "grupo": "24", "cuenta": "2408", "naturaleza": "credito",
     "descripcion": "IVA facturado en ventas"},
    {"codigo": "240815", "nombre": "Retención en la Fuente por Pagar (LEGACY)", "clase": 2, "grupo": "24", "cuenta": "2408", "naturaleza": "credito",
     "descripcion": "Cuenta legacy 240815 conservada para compatibilidad con flujos existentes de retefuente"},
    {"codigo": "2505", "nombre": "Salarios por Pagar", "clase": 2, "grupo": "25", "naturaleza": "credito",
     "descripcion": "Obligaciones laborales por salarios"},

    # ── Clase 3: PATRIMONIO (Naturaleza: Crédito) ──
    {"codigo": "3105", "nombre": "Capital Suscrito y Pagado", "clase": 3, "grupo": "31", "naturaleza": "credito",
     "descripcion": "Capital aportado por los socios"},
    {"codigo": "3115", "nombre": "Aportes Sociales", "clase": 3, "grupo": "31", "naturaleza": "credito",
     "descripcion": "Aportes de socios en SAS, Ltda, etc."},
    {"codigo": "3305", "nombre": "Reservas Obligatorias", "clase": 3, "grupo": "33", "naturaleza": "credito",
     "descripcion": "Reserva legal (10% de utilidades)"},
    {"codigo": "3605", "nombre": "Utilidad del Ejercicio", "clase": 3, "grupo": "36", "naturaleza": "credito",
     "descripcion": "Resultado neto del período actual"},
    {"codigo": "3705", "nombre": "Utilidades Acumuladas", "clase": 3, "grupo": "37", "naturaleza": "credito",
     "descripcion": "Utilidades de ejercicios anteriores no distribuidas"},

    # ── Clase 4: INGRESOS (Naturaleza: Crédito) ──
    {"codigo": "4135", "nombre": "Comercio al por Mayor y Menor", "clase": 4, "grupo": "41", "naturaleza": "credito",
     "descripcion": "Ingresos por venta de mercancías"},
    {"codigo": "4170", "nombre": "Servicios", "clase": 4, "grupo": "41", "naturaleza": "credito",
     "descripcion": "Ingresos por prestación de servicios"},
    {"codigo": "4175", "nombre": "Devoluciones en Ventas (DB)", "clase": 4, "grupo": "41", "naturaleza": "debito",
     "descripcion": "Devoluciones y descuentos en ventas"},
    {"codigo": "4210", "nombre": "Financieros", "clase": 4, "grupo": "42", "naturaleza": "credito",
     "descripcion": "Ingresos por rendimientos financieros"},

    # ── Clase 5: GASTOS (Naturaleza: Débito) ──
    {"codigo": "5105", "nombre": "Gastos de Personal", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Salarios, prestaciones, seguridad social"},
    {"codigo": "5110", "nombre": "Honorarios", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Pagos por servicios profesionales"},
    {"codigo": "5115", "nombre": "Impuestos", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Impuestos asumidos como gasto"},
    {"codigo": "5120", "nombre": "Arrendamientos", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Gastos por arriendo de inmuebles y equipos"},
    {"codigo": "5130", "nombre": "Seguros", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Primas de seguros"},
    {"codigo": "5135", "nombre": "Servicios Públicos", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Agua, energía, teléfono, internet"},
    {"codigo": "5140", "nombre": "Gastos Legales", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Gastos de notaría, registro, trámites legales"},
    {"codigo": "5195", "nombre": "Gastos Diversos", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Otros gastos no clasificados en cuentas específicas"},
    {"codigo": "5199", "nombre": "Provisiones", "clase": 5, "grupo": "51", "naturaleza": "debito",
     "descripcion": "Provisiones sobre cuentas por cobrar y otros activos"},
    {"codigo": "5305", "nombre": "Gastos Financieros", "clase": 5, "grupo": "53", "naturaleza": "debito",
     "descripcion": "Intereses, comisiones bancarias, GMF"},

    # ── Clase 6: COSTOS DE VENTA (Naturaleza: Débito) ──
    {"codigo": "6135", "nombre": "Costo de Venta - Comercio", "clase": 6, "grupo": "61", "naturaleza": "debito",
     "descripcion": "Costo de la mercancía vendida"},
    {"codigo": "6170", "nombre": "Costo de Venta - Servicios", "clase": 6, "grupo": "61", "naturaleza": "debito",
     "descripcion": "Costo de prestación de servicios"},
]


def seed_puc():
    """Insert PUC accounts into the database."""
    db = SessionLocal()
    try:
        inserted = 0
        skipped = 0

        for account_data in PUC_ACCOUNTS:
            existing = db.query(CuentaPUC).filter(
                CuentaPUC.codigo == account_data["codigo"]
            ).first()

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
        logger.info(f"PUC seed complete: {inserted} inserted, {skipped} skipped (already existed)")
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
