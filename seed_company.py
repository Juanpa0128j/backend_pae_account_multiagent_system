from dotenv import load_dotenv
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()
DATABASE_URL = os.environ['DATABASE_URL']
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

with Session() as db:
    existing = db.execute(text("SELECT nit FROM company_settings WHERE nit = '800999888'")).fetchone()
    if existing:
        print('800999888 already exists')
    else:
        db.execute(text('''
            INSERT INTO company_settings (
                nit, nombre, ciudad, codigo_ciiu, iva_responsable, es_declarante,
                tasa_retefuente_servicios, tasa_retefuente_bienes, tasa_retefuente_arrendamiento,
                tasa_reteica, tasa_iva_general, tasa_ica, tasa_renta
            ) VALUES (
                '800999888', 'Test Company', 'Bogota', '6311', true, true,
                0.040000, 0.025000, 0.035000, 0.006900, 0.190000, 0.006900, 0.350000
            )
        '''))
        db.commit()
        print('Inserted 800999888')
