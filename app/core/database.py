"""
SQLAlchemy database setup for PostgreSQL.
Provides engine, session factory, Base class, and FastAPI dependency.
"""

import os
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# Supabase free/shared poolers can hit connection limits quickly with large app pools.
is_supabase = (
    "supabase.co" in settings.database_url
    or "pooler.supabase.com" in settings.database_url
)
pool_size = 10 if is_supabase else 20
max_overflow = 15 if is_supabase else 25

# PostgreSQL engine with connection pooling.
# pool_timeout is generous: DB-write tasks queue via DB_WRITE_SEMAPHORE so they
# wait their turn rather than racing for connections; the pool timeout is a
# last-resort safety net, not the primary concurrency control.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=pool_size,
    max_overflow=max_overflow,
    pool_recycle=1800,
    pool_timeout=30,
    connect_args={"connect_timeout": 10},
    echo=False,
)

# Global semaphore that serializes DB-write phases of the agent pipeline.
# LLM extraction/parsing runs freely in parallel; only the persist step
# acquires this semaphore, so at most DB_WRITE_CONCURRENCY documents write
# to the database simultaneously — eliminating race conditions on shared rows
# and preventing connection-pool exhaustion under concurrent uploads.
DB_WRITE_CONCURRENCY = 1
DB_WRITE_SEMAPHORE = threading.Semaphore(DB_WRITE_CONCURRENCY)

# Global semaphore that limits concurrent ingest pipelines end-to-end.
# Each pipeline holds DB connections at multiple stages (status updates,
# classification reads, persistence). With Supabase pool_size+overflow=5,
# running >2 pipelines concurrently while the frontend polls every 2s
# starves the pool and hangs the backend. This semaphore queues uploads
# instead of running them all in parallel.
INGEST_PIPELINE_CONCURRENCY = int(os.getenv("INGEST_PIPELINE_CONCURRENCY", "1"))
INGEST_PIPELINE_SEMAPHORE = threading.Semaphore(INGEST_PIPELINE_CONCURRENCY)

# Global semaphore that limits concurrent process (accounting) pipelines.
# Contador, Tributario, Auditor agents + persist phase all compete for DB
# connections. With pool_size=10+overflow=15 (Supabase), we can safely run
# 4 processes concurrently without contention. This semaphore queues jobs.
PROCESS_PIPELINE_CONCURRENCY = int(os.getenv("PROCESS_PIPELINE_CONCURRENCY", "2"))
PROCESS_PIPELINE_SEMAPHORE = threading.Semaphore(PROCESS_PIPELINE_CONCURRENCY)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all ORM models
Base = declarative_base()


def get_db():
    """FastAPI dependency that provides a DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Verify PostgreSQL is reachable. Returns True if healthy."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def init_db():
    """Create all tables directly (for development/testing only)."""
    Base.metadata.create_all(bind=engine)
