import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.v1 import (
    ingest,
    process,
    reports,
    tax,
    evaluation,
    transactions,
    dashboard,
    books,
    settings as settings_router_mod,
    chat,
    puc as puc_router_mod,
    auth as auth_router_mod,
)
from app.core.config import settings
from app.core.database import check_db_connection
from app.core.exceptions import PAEException, DatabaseException

# Configure default thread pool executor for the event loop.
# This ensures health checks and other requests remain responsive
# even when the server is busy processing documents.
# The default executor is used by asyncio.to_thread() and other operations.
_loop = asyncio.get_event_loop()
_loop.set_default_executor(
    ThreadPoolExecutor(max_workers=8, thread_name_prefix="api_worker")
)

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Silence noisy 3rd-party loggers that flood the console:
# - httpx: Inngest dev server polls /api/inngest every ~5s
# - sqlalchemy.engine: per-statement DDL/DML at INFO level
# - urllib3 / httpcore: low-level HTTP plumbing
for _noisy in (
    "httpx",
    "httpcore",
    "urllib3",
    "sqlalchemy.engine",
    "sqlalchemy.engine.Engine",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    # Startup
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
        logger.info(f"LangSmith tracing enabled (project={settings.langsmith_project})")
    logger.info(f"Starting PAE Backend (env={settings.app_env})")
    db_ok = check_db_connection()
    if db_ok:
        logger.info("Database connection verified")
    else:
        logger.warning("Database connection failed — some features will be unavailable")
    yield
    # Shutdown
    logger.info("Application shutdown")


app = FastAPI(
    title="PAE Account Multiagent System API",
    description="Backend API for the PAE Account Multiagent System",
    version="0.2.0",
    lifespan=lifespan,
)

# Base development origins
origins = [
    "http://localhost:3000",  # Next.js dev server
    "http://localhost:5173",  # Vite dev server
    "http://localhost:5174",  # Alternative dev port
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

# Add production origins from settings (env var or .env file)
if settings.allowed_origins:
    env_origins = [
        origin.strip()
        for origin in settings.allowed_origins.split(",")
        if origin.strip()
    ]
    origins.extend(env_origins)
    logger.info("CORS allowed_origins configured: %s", env_origins)
else:
    logger.warning("ALLOWED_ORIGINS not set — only local dev origins enabled")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Health"])
async def root():
    return {
        "message": "PAE Account Multiagent System API is running",
        "status": "healthy",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    db_ok = check_db_connection()
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "environment": settings.app_env,
    }


# Exception handlers
@app.exception_handler(PAEException)
async def pae_exception_handler(request: Request, exc: PAEException):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "error_type": type(exc).__name__},
    )


@app.exception_handler(DatabaseException)
async def db_exception_handler(request: Request, exc: DatabaseException):
    logger.error(f"Database error: {exc}")
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Database service unavailable",
            "error_type": "DatabaseException",
        },
    )


# Include routers
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["Ingesta"])
app.include_router(process.router, prefix="/api/v1/process", tags=["Procesamiento"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["Reportes"])
app.include_router(tax.router, prefix="/api/v1/tax", tags=["Tributario"])
app.include_router(evaluation.router, prefix="/api/v1/evaluation", tags=["Evaluación"])
app.include_router(
    transactions.router, prefix="/api/v1/transactions", tags=["Transacciones"]
)
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(books.router, prefix="/api/v1/books", tags=["Libros"])
app.include_router(
    settings_router_mod.router, prefix="/api/v1/settings", tags=["Configuración"]
)
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat Financiero"])
app.include_router(puc_router_mod.router, prefix="/api/v1/puc", tags=["PUC"])
app.include_router(auth_router_mod.router, prefix="/api/v1/auth", tags=["Auth"])

# --- Inngest durable workflow engine (flag-gated mount) ---
if settings.workflow_engine == "inngest":
    import inngest.fast_api

    from app.workflows.functions.ingest_pipeline import ingest_pipeline
    from app.workflows.functions.process_pipeline import process_pipeline
    from app.workflows.inngest_client import get_inngest_client

    inngest.fast_api.serve(
        app,
        get_inngest_client(),
        [process_pipeline, ingest_pipeline],
    )
    logger.info("Inngest serve mounted at /api/inngest (functions: process, ingest)")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
