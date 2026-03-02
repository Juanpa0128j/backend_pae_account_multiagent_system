"""
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
