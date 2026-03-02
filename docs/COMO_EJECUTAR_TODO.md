# Cómo ejecutar todo (local)

Esta guía cubre el flujo completo para correr el proyecto en local con `uv`.

## 1) Prerrequisitos

- Python 3.11+
- `uv` instalado
- PostgreSQL 17 instalado localmente

## 2) Instalar dependencias

Desde la raíz del proyecto:

```bash
uv sync
```

## 3) Levantar PostgreSQL

Verificar estado:

```bash
pg_isready -h localhost -p 5432
```

Si no está arriba, iniciar cluster (Debian/Ubuntu):

```bash
sudo pg_ctlcluster 17 main start
```

## 4) Configuración de entorno

Asegúrate de tener estas variables (por `.env` o entorno):

```bash
DATABASE_URL=postgresql://pae_user:password@localhost:5432/pae_accounting
GEMINI_API_KEY=tu_api_key
ENVIRONMENT=development
```

> Nota: `GOOGLE_API_KEY` también funciona como fallback en el cliente Gemini.

## 5) Migraciones y datos base

Aplicar migraciones:

```bash
uv run alembic upgrade head
```

Sembrar plan de cuentas PUC:

```bash
uv run python scripts/seed_puc.py
```

## 6) Levantar API

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Verificar health:

```bash
curl http://127.0.0.1:8000/health
```

## 7) Correr tests

Suite completa:

```bash
uv run pytest -q
```

Tests principales de DB + integración:

```bash
uv run pytest tests/test_database.py tests/test_agent_integration.py -v
```

## 8) Scripts manuales (opcional)

Estos no hacen parte de la suite automática de `pytest`:

```bash
uv run python tests/test_agent_quick.py
uv run python tests/test_api_endpoints.py
```

## 9) Apagar servicios

Detener API: `Ctrl+C` en la terminal donde corre `uvicorn`.

Detener PostgreSQL (si aplica):

```bash
sudo pg_ctlcluster 17 main stop
```
