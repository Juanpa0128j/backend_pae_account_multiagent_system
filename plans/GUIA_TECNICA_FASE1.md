# Guía Técnica de Implementación - Fase 1 & Inicio Fase 2

**Objetivo:** Tutorial paso-a-paso con código listo para copiar y ejecutar.

---

## Fase 1: Fundamentos - Guía de Ejecución

### Paso 1: Actualizar `pyproject.toml`

Reemplazar el contenido con:

```toml
[project]
name = "backend-pae-account-multiagent-system"
version = "0.1.0"
description = "Multiagent system for Colombian accounting automation"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.129.0",
    "uvicorn[standard]>=0.40.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "sqlalchemy>=2.0.0",
    "alembic>=1.13.0",
    "chromadb>=0.4.0",
    "langchain>=0.1.0",
    "langgraph>=0.1.0",
    "langchain-google-genai>=0.1.0",
    "python-dotenv>=1.0.0",
    "python-multipart>=0.0.22",
    "pypdf>=6.7.0",
    "openpyxl>=3.11.0",
    "pandas>=2.0.0",
    "pydantic-extra-types>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "httpx>=0.25.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "mypy>=1.7.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Luego ejecutar:**
```bash
pip install -e ".[dev]"
```

---

### Paso 2: Crear `.env.example` y `.env`

**Archivo:** `.env.example`
```bash
# Database
DATABASE_URL=sqlite:///./test.db
# Para PostgreSQL cuando pases a prod: 
# DATABASE_URL=postgresql://user:password@localhost:5432/pae_db

# Gemini API
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# App Config
APP_ENV=development
LOG_LEVEL=INFO
SECRET_KEY=your-secret-key-here-change-in-production

# Storage
UPLOAD_FOLDER=./storage/uploads
```

**Luego crear `.env` copiando y rellenando los valores.**

---

### Paso 3: Crear `app/core/config.py`

```python
"""
Global configuration using Pydantic Settings.
All env variables are validated and typed here.
"""

from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///./test.db"
    
    # API
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    log_level: str = "INFO"
    
    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"
    
    # Storage
    upload_folder: str = "./storage/uploads"
    
    # Paths
    base_path: Path = Path(__file__).parent.parent.parent
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
```

---

### Paso 4: Crear `app/core/logger.py`

```python
"""
Structured logging configuration.
"""

import logging
import json
from datetime import datetime
from app.core.config import settings

class JSONFormatter(logging.Formatter):
    """Format logs as JSON for better parsing."""
    
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance."""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = JSONFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(settings.log_level)
    
    return logger
```

---

### Paso 5: Crear `app/core/exceptions.py`

```python
"""
Custom exceptions for the application.
"""

class PAEException(Exception):
    """Base exception for PAE system."""
    pass

class InvalidNITException(PAEException):
    """Invalid NIT format."""
    pass

class FileProcessingException(PAEException):
    """Error processing file."""
    pass

class ProcessNotFoundException(PAEException):
    """Process ID not found."""
    pass

class ValidationException(PAEException):
    """Data validation error."""
    pass

class AgentException(PAEException):
    """Error in agent execution."""
    pass

class RAGException(PAEException):
    """Error in RAG retrieval."""
    pass
```

---

### Paso 6: Crear `app/core/database.py` (ORM Setup)

```python
"""
SQLAlchemy database setup and utilities.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# Create engine
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    echo=(settings.app_env == "development")
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all models
Base = declarative_base()

def get_db():
    """Dependency for FastAPI to inject DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initialize database (create all tables)."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized")
```

---

### Paso 7: Crear `app/models/database.py` (ORM Models)

```python
"""
SQLAlchemy ORM models for the database.
"""

from sqlalchemy import Column, String, Float, DateTime, Enum, Text, JSON, Integer
from sqlalchemy.sql import func
from datetime import datetime
import enum
from app.core.database import Base

class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    POSTED = "posted"
    REJECTED = "rejected"
    ERROR = "error"

class ProcessStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TransactionPending(Base):
    """Raw transactions extracted from ingestion."""
    __tablename__ = "transactions_pending"
    
    id = Column(String, primary_key=True, index=True)
    ingest_id = Column(String, index=True)
    file_name = Column(String)
    
    # Raw extracted data
    fecha = Column(DateTime)
    nit_emisor = Column(String, index=True)
    nit_receptor = Column(String, index=True)
    total = Column(Float)
    
    # Metadata
    raw_data = Column(JSON)  # Store full extracted JSON
    status = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class TransactionPosted(Base):
    """Fully processed transactions with PUC and taxes."""
    __tablename__ = "transactions_posted"
    
    id = Column(String, primary_key=True, index=True)
    transaction_pending_id = Column(String, index=True)
    
    # Classification
    cuenta_puc = Column(String, index=True)
    descripcion = Column(Text)
    
    # Taxes
    retefuente = Column(Float, default=0)
    reteica = Column(Float, default=0)
    iva = Column(Float, default=0)
    
    # Journal entries (asiento contable)
    journal_entries = Column(JSON)  # List of {cuenta, debito, credito}
    
    # Metadata
    agent_log = Column(JSON)  # Log of agent decisions and reasoning
    status = Column(Enum(TransactionStatus), default=TransactionStatus.POSTED)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ProcessJob(Base):
    """Track async processing jobs."""
    __tablename__ = "process_jobs"
    
    id = Column(String, primary_key=True, index=True)
    ingest_id = Column(String, index=True)
    
    status = Column(Enum(ProcessStatus), default=ProcessStatus.QUEUED)
    current_stage = Column(String)  # "ingest", "contador", "tributario", "auditor"
    
    error_message = Column(Text, nullable=True)
    progress = Column(Integer, default=0)  # 0-100
    
    agent_log = Column(JSON)  # Timeline of agent steps
    
    created_at = Column(DateTime, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

class AuditLog(Base):
    """Immutable log of all system actions (for compliance)."""
    __tablename__ = "audit_logs"
    
    id = Column(String, primary_key=True, index=True)
    action = Column(String)  # "transaction_created", "transaction_updated", "agent_ran"
    entity_id = Column(String)
    entity_type = Column(String)  # "transaction", "job"
    
    details = Column(JSON)
    
    created_at = Column(DateTime, server_default=func.now())
```

---

### Paso 8: Crear `app/core/vectordb.py` (ChromaDB Setup)

```python
"""
Vector database setup with ChromaDB.
Manages normative and operational RAG.
"""

import chromadb
from pathlib import Path
from app.core.logger import get_logger

logger = get_logger(__name__)

class VectorDB:
    def __init__(self):
        # Local ChromaDB persistence
        db_path = Path("./storage/chromadb")
        db_path.mkdir(parents=True, exist_ok=True)
        
        self.client = chromadb.PersistentClient(path=str(db_path))
        
        # Get or create collections
        self.normativa_col = self.client.get_or_create_collection(
            name="normativa_colombia",
            metadata={"description": "Colombian tax law and accounting norms"}
        )
        
        self.empresa_docs_col = self.client.get_or_create_collection(
            name="empresa_docs",
            metadata={"description": "Company documents and invoices"}
        )
        
        logger.info("VectorDB initialized with ChromaDB")
    
    def add_normativo(self, doc_id: str, content: str, metadata: dict):
        """Add a normative document (law, article, etc)."""
        self.normativa_col.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata]
        )
    
    def search_normativo(self, query: str, limit: int = 3):
        """Search normative documents."""
        results = self.normativa_col.query(
            query_texts=[query],
            n_results=limit
        )
        return results
    
    def add_empresa_doc(self, doc_id: str, content: str, metadata: dict):
        """Add a company document."""
        self.empresa_docs_col.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadata]
        )
    
    def search_empresa(self, query: str, limit: int = 3):
        """Search company documents."""
        results = self.empresa_docs_col.query(
            query_texts=[query],
            n_results=limit
        )
        return results

# Singleton instance
vectordb = VectorDB()
```

---

### Paso 9: Crear `app/core/gemini_client.py`

```python
"""
Gemini API client wrapper for agent interactions.
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

def get_gemini_model():
    """Get Gemini model instance via LangChain."""
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.0,
    )

async def call_gemini(prompt: str, system_prompt: str = None):
    """
    Call Gemini with optional system prompt.
    Returns: response text
    """
    try:
        model = get_gemini_model()
        
        if system_prompt:
            message = f"{system_prompt}\n\n{prompt}"
        else:
            message = prompt
        
        response = model.invoke([HumanMessage(content=message)])
        logger.info(f"Gemini call successful")
        return response.content
        
    except Exception as e:
        logger.error(f"Gemini error: {str(e)}")
        raise
```

---

### Paso 10: Inicializar Alembic

```bash
# En la raíz del proyecto
cd d:\Code\Github\backend_pae_account_multiagent_system
alembic init alembic
```

**Editar `alembic/env.py` para auto-detectar modelos:**

```python
# Línea ~21, en la función run_migrations_offline():
# Y en run_migrations_online():

from app.core.database import Base
target_metadata = Base.metadata
```

**Primera migración:**
```bash
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

---

## Fase 2: APIs y Agente Piloto - Inicio Rápido

### Paso 11: Crear `app/models/schemas.py` (Pydantic modelos)

```python
"""
Pydantic schemas for request/response validation.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

# Enums
class TransactionStatusEnum(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    POSTED = "posted"
    REJECTED = "rejected"
    ERROR = "error"

class ProcessStatusEnum(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

# Ingest
class IngestResponse(BaseModel):
    message: str
    ingest_id: str
    status: str
    file_name: str
    extracted_transactions: int = 0

class IngestDetailResponse(BaseModel):
    ingest_id: str
    file_name: str
    status: str
    created_at: datetime
    raw_transactions: List[Dict[str, Any]]

# Transactions
class RawTransaction(BaseModel):
    fecha: datetime
    nit_emisor: str
    nit_receptor: str
    total: float
    descripcion: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None

class Transaction(RawTransaction):
    cuenta_puc: Optional[str] = None
    puc_description: Optional[str] = None

class TransactionWithTax(Transaction):
    retefuente: float = 0
    reteica: float = 0
    iva: float = 0
    tax_references: Optional[List[str]] = None  # Art. del ET

class JournalEntry(BaseModel):
    cuenta: str
    debito: float = 0
    credito: float = 0

class ProcessResponse(BaseModel):
    message: str
    process_id: str
    ingest_id: str
    status: str

class ProcessStatusResponse(BaseModel):
    process_id: str
    status: ProcessStatusEnum
    current_stage: Optional[str] = None
    progress: int
    error_message: Optional[str] = None
    agent_log: Optional[List[Dict[str, Any]]] = None

# Reports
class ReportResponse(BaseModel):
    report_type: str
    period: Optional[str] = None
    data: Dict[str, Any]
    generated_at: datetime

class TaxResponse(BaseModel):
    report_type: str
    data: Dict[str, Any]
    references: Optional[List[str]] = None

class EvaluationResponse(BaseModel):
    status: str
    metrics: Dict[str, float]
    evaluated_at: datetime

# Validators
class Transaction(BaseModel):
    @validator('nit_emisor', 'nit_receptor')
    def validate_nit(cls, v):
        if not v or len(v) < 5:
            raise ValueError('Invalid NIT format')
        return v
    
    @validator('total')
    def validate_total(cls, v):
        if v <= 0:
            raise ValueError('Total must be positive')
        return v
```

---

### Paso 12: Crear `app/agents/state.py`

```python
"""
LangGraph state definition.
This is the shared context between all agents.
"""

from typing import TypedDict, List, Optional, Dict, Any
from datetime import datetime

class RawTransactionData(TypedDict):
    fecha: datetime
    nit_emisor: str
    nit_receptor: str
    total: float
    descripcion: Optional[str]
    items: Optional[List[Dict[str, Any]]]

class AgentState(TypedDict):
    """Shared state across all agents in LangGraph."""
    
    # Input
    ingest_id: str
    file_path: str
    file_name: str
    
    # Processing data
    raw_transactions: List[RawTransactionData]
    classified_transactions: List[Dict[str, Any]]
    tax_applied_transactions: List[Dict[str, Any]]
    journal_entries: List[Dict[str, Any]]
    
    # Agent lifecycle
    current_stage: str  # "ingest", "contador", "tributario", "auditor", "posted"
    current_agent: Optional[str]
    
    # Metadata
    errors: List[str]
    agent_log: List[Dict[str, Any]]  # Timeline of agent steps
    
    # Control
    retry_count: int
    max_retries: int
    should_reject: bool
    rejection_reason: Optional[str]
```

---

### Paso 13: Crear `app/agents/supervisor.py` (Orquestador básico)

```python
"""
Supervisor agent - orchestrates the workflow.
Routes between different worker agents based on state.
"""

from app.agents.state import AgentState
from app.core.logger import get_logger

logger = get_logger(__name__)

def supervisor_node(state: AgentState) -> AgentState:
    """
    Route logic: decide which worker agent runs next.
    """
    
    logger.info(f"Supervisor evaluating state: {state['current_stage']}")
    
    # Determine next step
    if state['current_stage'] == "init":
        next_agent = "ingest"
        state['current_stage'] = "ingest"
        
    elif state['current_stage'] == "ingest":
        if state['errors']:
            state['current_stage'] = "error"
            next_agent = "END"
        else:
            next_agent = "contador"
            state['current_stage'] = "proceso"
    
    elif state['current_stage'] == "proceso":
        if not state['classified_transactions']:
            state['current_stage'] = "error"
            next_agent = "END"
        else:
            next_agent = "tributario"
    
    elif state['current_stage'] == "tributary_done":
        next_agent = "auditor"
        state['current_stage'] = "audit"
    
    elif state['current_stage'] == "audit":
        if state['should_reject']:
            logger.warning(f"Auditor rejected: {state['rejection_reason']}")
            if state['retry_count'] < state['max_retries']:
                state['retry_count'] += 1
                next_agent = "contador"
                state['current_stage'] = "proceso"
            else:
                state['current_stage'] = "rejected"
                next_agent = "END"
        else:
            next_agent = "END"
            state['current_stage'] = "posted"
    
    else:
        next_agent = "END"
    
    # Log the decision
    state['agent_log'].append({
        "agent": "supervisor",
        "action": f"routed_to_{next_agent}",
        "timestamp": str(datetime.now()),
        "current_stage": state['current_stage']
    })
    
    state['current_agent'] = next_agent
    return state

def should_continue(state: AgentState) -> str:
    """
    Conditional edge: determine if we should continue or end.
    """
    if state['current_agent'] == "END":
        return "end"
    return "continue"
```

---

### Próximos Pasos en Orden de Ejecución

1. ✅ Actualizar `pyproject.toml` y instalar dependen cias
2. ✅ Crear `.env` y `.env.example`
3. ✅ Crear archivos en `app/core/` (config, logger, exceptions, database, vectordb, gemini_client)
4. ✅ Crear modelos ORM en `app/models/database.py`
5. ✅ Inicializar Alembic y crear primera migración
6. ✅ Crear schemas Pydantic en `app/models/schemas.py`
7. ✅ Crear state y supervisor para LangGraph
8. ⏭️ Crear agente de ingesta (`app/agents/ingest_agent.py`)
9. ⏭️ Actualizar endpoints en `app/api/v1/`
10. ⏭️ Implementar `POST /ingest/upload` completo

---

**Duración Estimada: Fase 1 = 4 semanas**  
**Documentado por:** Juan Pablo Mejia Gomez  
**Última actualización:** 2026-02-18

