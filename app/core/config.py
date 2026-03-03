"""
<<<<<<< HEAD
Centralised application settings via Pydantic BaseSettings.
All modules should import `get_settings()` instead of reading env vars directly.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # --- Gemini / Google AI ------------------------------------------------
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_embedding_model: str = Field(
        "models/gemini-embedding-001", alias="GEMINI_EMBEDDING_MODEL"
    )

    # --- ChromaDB ----------------------------------------------------------
    chroma_persist_path: str = Field("./storage/chromadb", alias="CHROMA_PERSIST_PATH")

    # --- FastAPI -----------------------------------------------------------
    port: int = Field(8000, alias="PORT")
    host: str = Field("0.0.0.0", alias="HOST")
    api_v1_str: str = Field("/api/v1", alias="API_V1_STR")

    # --- Security ----------------------------------------------------------
    secret_key: str = Field("dev-secret-change-in-prod", alias="SECRET_KEY")

    # --- Database ----------------------------------------------------------
    database_url: str = Field("sqlite:///./storage/pae.db", alias="DATABASE_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (created once per process)."""
    return Settings()
=======
Global configuration using Pydantic Settings.
All env variables are validated and typed here.
PostgreSQL is the only supported database engine.
"""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database (PostgreSQL only)
    database_url: str = "postgresql://pae_user:password@localhost:5432/pae_accounting"

    # API
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    log_level: str = "INFO"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    def model_post_init(self, __context) -> None:
        """Fix Render's postgres:// scheme → postgresql:// for SQLAlchemy 2.0."""
        if self.database_url.startswith("postgres://"):
            object.__setattr__(
                self,
                "database_url",
                self.database_url.replace("postgres://", "postgresql://", 1),
            )

    # Storage
    upload_folder: str = "./storage/uploads"

    # Paths
    base_path: Path = Path(__file__).parent.parent.parent

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "extra": "ignore",
    }

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
>>>>>>> 3f3e634c6391367c44ac20ed8783f0e8ac2067e0
