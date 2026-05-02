"""
SQLAlchemy database setup for PostgreSQL.
Provides engine, session factory, Base class, and FastAPI dependency.
"""

import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# Supabase free/shared poolers can hit connection limits quickly with large app pools.
is_supabase = (
    "supabase.co" in settings.database_url
    or "pooler.supabase.com" in settings.database_url
)
pool_size = 2 if is_supabase else 5
max_overflow = 3 if is_supabase else 10

# PostgreSQL engine with connection pooling.
# pool_timeout is generous: DB-write tasks queue via DB_WRITE_SEMAPHORE so they
# wait their turn rather than racing for connections; the pool timeout is a
# last-resort safety net, not the primary concurrency control.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=pool_size,
    max_overflow=max_overflow,
    pool_recycle=300,
    pool_timeout=120,
    connect_args={"connect_timeout": 60},
    echo=(settings.app_env == "development"),
)

# Global semaphore that serializes DB-write phases of the agent pipeline.
# LLM extraction/parsing runs freely in parallel; only the persist step
# acquires this semaphore, so at most DB_WRITE_CONCURRENCY documents write
# to the database simultaneously — eliminating race conditions on shared rows
# and preventing connection-pool exhaustion under concurrent uploads.
DB_WRITE_CONCURRENCY = 1
DB_WRITE_SEMAPHORE = threading.Semaphore(DB_WRITE_CONCURRENCY)

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
