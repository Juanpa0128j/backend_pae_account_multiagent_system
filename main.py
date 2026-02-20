import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import ingest, process, reports, tax, evaluation, transactions, dashboard, books

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PAE Account Multiagent System API",
    description="Backend API for the PAE Account Multiagent System",
    version="0.1.0",
)

# Base development origins
origins = [
    "http://localhost:3000",      # Next.js dev server
    "http://localhost:5173",      # Vite dev server
    "http://localhost:5174",      # Alternative dev port
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

# Add production origins from environment variable if present
env_origins = os.getenv("ALLOWED_ORIGINS", "")
if env_origins:
    origins.extend([origin.strip() for origin in env_origins.split(",") if origin.strip()])

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
    return {"message": "PAE Account Multiagent System API is running", "status": "healthy"}

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}

# Include routers
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["Ingesta"])
app.include_router(process.router, prefix="/api/v1/process", tags=["Procesamiento"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["Reportes"])
app.include_router(tax.router, prefix="/api/v1/tax", tags=["Tributario"])
app.include_router(evaluation.router, prefix="/api/v1/evaluation", tags=["Evaluación"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["Transacciones"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["Dashboard"])
app.include_router(books.router, prefix="/api/v1/books", tags=["Libros"])

@app.on_event("startup")
async def startup_event():
    logger.info("Application startup")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
