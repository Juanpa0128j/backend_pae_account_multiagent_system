"""
Centralized application settings via Pydantic BaseSettings.
All env variables are validated and typed here.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # --- Gemini / Google AI ------------------------------------------------
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")

    # --- OpenAI (first fallback when Gemini quota is exhausted) ------------
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4.1-nano", alias="OPENAI_MODEL")

    # --- Groq (second fallback) --------------------------------------------
    groq_api_key: str = Field("", alias="GROQ_API_KEY")
    groq_model: str = Field("openai/gpt-oss-20b", alias="GROQ_MODEL")

    # --- HuggingFace API (embeddings + reranker) ---------------------------
    huggingface_api_key: str = Field("", alias="HUGGINGFACE_API_KEY")

    # --- LlamaCloud API ----------------------------------------------------
    llama_cloud_api_key: str = Field("", alias="LLAMA_CLOUD_API_KEY")

    # --- Database (PostgreSQL only) ----------------------------------------
    database_url: str = Field(
        "postgresql://pae_user:password@localhost:5432/pae_accounting",
        alias="DATABASE_URL",
    )

    # --- App Config --------------------------------------------------------
    app_env: str = Field("development", alias="APP_ENV")
    secret_key: str = Field("change-me-in-production", alias="SECRET_KEY")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- FastAPI -----------------------------------------------------------
    port: int = Field(8000, alias="PORT")
    host: str = Field("0.0.0.0", alias="HOST")
    api_v1_str: str = Field("/api/v1", alias="API_V1_STR")

    # --- Storage -----------------------------------------------------------
    upload_folder: str = Field("./storage/uploads", alias="UPLOAD_FOLDER")

    # --- Paths -------------------------------------------------------------
    base_path: Path = Path(__file__).parent.parent.parent

    # --- CORS --------------------------------------------------------------
    allowed_origins: str = Field(
        "http://localhost:3000,http://localhost:5173", alias="ALLOWED_ORIGINS"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        """Normalize DB URL and enforce SSL for Supabase-hosted Postgres."""
        normalized_url = self.database_url

        if normalized_url.startswith("postgres://"):
            normalized_url = normalized_url.replace("postgres://", "postgresql://", 1)

        is_supabase = (
            "supabase.co" in normalized_url or "pooler.supabase.com" in normalized_url
        )
        if is_supabase and "sslmode=" not in normalized_url:
            separator = "&" if "?" in normalized_url else "?"
            normalized_url = f"{normalized_url}{separator}sslmode=require"

        if normalized_url != self.database_url:
            object.__setattr__(self, "database_url", normalized_url)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()


def get_settings():
    return settings
