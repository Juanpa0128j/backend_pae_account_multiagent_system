"""
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
        """Normalize DB URL and enforce SSL for Supabase-hosted Postgres."""
        normalized_url = self.database_url

        if normalized_url.startswith("postgres://"):
            normalized_url = normalized_url.replace("postgres://", "postgresql://", 1)

        is_supabase = "supabase.co" in normalized_url or "pooler.supabase.com" in normalized_url
        if is_supabase and "sslmode=" not in normalized_url:
            separator = "&" if "?" in normalized_url else "?"
            normalized_url = f"{normalized_url}{separator}sslmode=require"

        if normalized_url != self.database_url:
            object.__setattr__(self, "database_url", normalized_url)

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
