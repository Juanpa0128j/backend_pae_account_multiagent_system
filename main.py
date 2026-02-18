import logging
from fastapi import FastAPI
from app.api.v1 import ingest, process, reports, tax, evaluation

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

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}

# Include routers
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["Ingesta"])
app.include_router(process.router, prefix="/api/v1/process", tags=["Procesamiento"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["Reportes"])
app.include_router(tax.router, prefix="/api/v1/tax", tags=["Tributario"])
app.include_router(evaluation.router, prefix="/api/v1/evaluation", tags=["Evaluación"])

@app.on_event("startup")
async def startup_event():
    logger.info("Application startup")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
