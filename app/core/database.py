"""
SQLAlchemy database setup for PostgreSQL.
Provides engine, session factory, Base class, and FastAPI dependency.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# Supabase free/shared poolers can hit connection limits quickly with large app pools.
is_supabase = "supabase.co" in settings.database_url or "pooler.supabase.com" in settings.database_url
pool_size = 2 if is_supabase else 5
max_overflow = 3 if is_supabase else 10

# PostgreSQL engine with connection pooling
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=pool_size,
    max_overflow=max_overflow,
    pool_recycle=300,
    connect_args={"connect_timeout": 60},
    echo=(settings.app_env == "development"),
)

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
