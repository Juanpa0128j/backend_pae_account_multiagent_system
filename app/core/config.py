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
    openai_model: str = Field("gpt-4.1-mini", alias="OPENAI_MODEL")
    # Document classifier uses a stronger model — the pre-refactor
    # doc_classifier hardcoded gpt-4o-mini for this task and classification
    # accuracy regressed when the main extraction model (nano) took over.
    openai_classifier_model: str = Field("gpt-4o-mini", alias="OPENAI_CLASSIFIER_MODEL")

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
    supabase_jwt_secret: str = Field("", alias="SUPABASE_JWT_SECRET")
    supabase_url: str = Field("", alias="SUPABASE_URL")
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

    # --- LangSmith (observability / tracing) -------------------------------
    langsmith_tracing: bool = Field(False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str = Field("", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field("PAE Agentes", alias="LANGSMITH_PROJECT")
    langchain_project: str = Field("PAE Agentes", alias="LANGCHAIN_PROJECT")
    langsmith_endpoint: str = Field(
        "https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT"
    )

    # --- Inngest (durable workflows) ---------------------------------------
    workflow_engine: str = Field("inline", alias="WORKFLOW_ENGINE")
    inngest_app_id: str = Field("pae-backend", alias="INNGEST_APP_ID")
    inngest_event_key: str = Field("", alias="INNGEST_EVENT_KEY")
    inngest_signing_key: str = Field("", alias="INNGEST_SIGNING_KEY")
    inngest_dev: bool = Field(True, alias="INNGEST_DEV")
    inngest_is_production: bool | None = Field(None, alias="INNGEST_IS_PRODUCTION")
    inngest_concurrency_per_nit: int = Field(5, alias="INNGEST_CONCURRENCY_PER_NIT")
    inngest_openai_throttle_rpm: int = Field(400, alias="INNGEST_OPENAI_THROTTLE_RPM")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        """Normalize DB URL, enforce SSL for Supabase, and reject insecure prod config."""
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

        # Refuse to boot in production with a default/empty SECRET_KEY. Better to
        # crash now than expose signed cookies/tokens with a published value.
        if self.app_env == "production":
            insecure_keys = {"", "change-me-in-production", "changeme", "secret"}
            if self.secret_key in insecure_keys or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a strong (>=32 chars) random value "
                    "when APP_ENV=production. Refusing to start with the default."
                )

        if self.app_env == "production" and self.workflow_engine == "inngest":
            if not self.inngest_event_key or not self.inngest_signing_key:
                raise ValueError(
                    "INNGEST_EVENT_KEY and INNGEST_SIGNING_KEY must be set when "
                    "WORKFLOW_ENGINE=inngest in production."
                )

        if (
            self.app_env == "production"
            and self.workflow_engine == "inngest"
            and self.inngest_dev
        ):
            raise ValueError(
                "INNGEST_DEV must be false when APP_ENV=production and "
                "WORKFLOW_ENGINE=inngest. Signature verification is disabled "
                "when INNGEST_DEV=true."
            )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()


def get_settings():
    return settings
