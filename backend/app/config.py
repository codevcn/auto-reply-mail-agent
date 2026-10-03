"""Application configuration settings using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for Mail Agent application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Environment
    ENVIRONMENT: str = Field(default="development", description="Environment: development, test, production")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    JSON_LOGS: bool = Field(default=False, description="Format logs as structured JSON")

    # Database Settings
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./mail_agent_dev.db",
        description="Async database connection URL (PostgreSQL asyncpg or SQLite aiosqlite)",
    )
    DB_ECHO: bool = Field(default=False, description="Echo SQL statements")

    # Security & Session Settings
    SECRET_KEY: str = Field(
        default="mail_agent_development_secret_key_min_32_bytes_long_change_in_production",
        description="Master secret key for cookie signing and crypto operations",
    )
    MASTER_ENCRYPTION_KEY: str = Field(
        default="mail_agent_envelope_master_encryption_key_32_bytes!",
        description="Master key for AES-256-GCM envelope encryption at rest",
    )
    SECRET_KEY_VERSION: str = Field(
        default="v1",
        description="Active master encryption key version",
    )
    SESSION_COOKIE_NAME: str = Field(default="session_id", description="Name of the session cookie")
    SESSION_COOKIE_SECURE: bool = Field(default=False, description="Secure flag for session cookie")
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = Field(
        default="lax", description="SameSite policy for session cookie"
    )
    SESSION_MAX_AGE_SECONDS: int = Field(default=7 * 24 * 3600, description="Session lifetime in seconds (7 days)")

    # Argon2id Hashing Parameters (RFC 9106 / OWASP recommended)
    ARGON2_TIME_COST: int = Field(default=2, description="Number of passes (iterations)")
    ARGON2_MEMORY_COST: int = Field(default=65536, description="Memory cost in KiB (64 MiB)")
    ARGON2_PARALLELISM: int = Field(default=1, description="Number of threads")
    ARGON2_HASH_LEN: int = Field(default=32, description="Output hash length in bytes")
    ARGON2_SALT_LEN: int = Field(default=16, description="Salt length in bytes")

    # CORS Settings
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8090"],
        description="Allowed CORS origins",
    )

    # Attachment Settings (Invariant R-20)
    ATTACHMENT_MAX_SINGLE_BYTES: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum allowed size for a single attachment in bytes (default: 10MB)",
    )
    ATTACHMENT_MAX_TOTAL_BYTES: int = Field(
        default=25 * 1024 * 1024,
        description="Maximum allowed total size for all attachments in an email (default: 25MB)",
    )
    ATTACHMENT_MAX_COUNT: int = Field(
        default=10,
        description="Maximum allowed number of attachments per email (default: 10)",
    )

    # AI Provider Settings (Invariant R-19 & R-07)
    AI_PROVIDER_TYPE: str = Field(
        default="mock",
        description="Active AI Provider type: mock, vertex, vertex_gemini, or gemini",
    )
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Google AI Studio Gemini API key (alternative to Vertex AI Service Account)",
    )
    VERTEX_AI_PROJECT_ID: str = Field(
        default="wrydeco-mail-agent",
        description="Google Cloud project ID for Vertex AI",
    )
    VERTEX_AI_LOCATION: str = Field(
        default="us-central1",
        description="Google Cloud region for Vertex AI",
    )
    VERTEX_AI_CLASSIFICATION_MODEL: str = Field(
        default="gemini-2.0-flash",
        description="Vertex AI model identifier for classification (e.g. gemini-2.0-flash, gemini-1.5-flash)",
    )
    VERTEX_AI_DRAFTING_MODEL: str = Field(
        default="gemini-2.0-flash",
        description="Vertex AI model identifier for reply draft generation (e.g. gemini-2.0-flash, gemini-1.5-pro)",
    )
    VERTEX_AI_CREDENTIALS_PATH: str | None = Field(
        default=None,
        description="File path to Google Cloud Service Account JSON key (read-only mounted)",
    )
    VERTEX_AI_TIMEOUT_SECONDS: float = Field(
        default=30.0,
        description="Timeout in seconds for Vertex AI HTTP requests",
    )

    @model_validator(mode="after")
    def resolve_gcp_fallbacks(self) -> "Settings":
        import os

        if self.VERTEX_AI_PROJECT_ID == "wrydeco-mail-agent" and os.environ.get("GCP_PROJECT_ID"):
            self.VERTEX_AI_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
        if self.VERTEX_AI_LOCATION == "us-central1" and os.environ.get("GCP_LOCATION"):
            self.VERTEX_AI_LOCATION = os.environ["GCP_LOCATION"]
        if not self.VERTEX_AI_CREDENTIALS_PATH and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            self.VERTEX_AI_CREDENTIALS_PATH = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        if not self.GEMINI_API_KEY and os.environ.get("GEMINI_API_KEY"):
            self.GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
        return self

    # Retention Settings (Invariant R-34: strictly <= 120 days)
    RETENTION_DAYS: int = Field(
        default=120,
        ge=1,
        le=120,
        description="Maximum data retention period in days (Invariant R-34: strictly <= 120 days)",
    )
    RETENTION_BATCH_SIZE: int = Field(
        default=500,
        ge=50,
        le=5000,
        description="Batch chunk size for transactional deletion to avoid PostgreSQL table locks",
    )
    RETENTION_SCHEDULER_INTERVAL_HOURS: int = Field(
        default=24,
        ge=1,
        description="Interval in hours for the automated retention background cleaner",
    )

    @field_validator("RETENTION_DAYS")
    @classmethod
    def enforce_max_retention_days(cls, v: int) -> int:
        """Enforces Invariant R-34: retention can never exceed 120 days."""
        if v > 120:
            raise ValueError("Invariant R-34 violation: RETENTION_DAYS cannot exceed 120 days.")
        if v < 1:
            raise ValueError("RETENTION_DAYS must be at least 1 day.")
        return v


@lru_cache
def get_settings() -> Settings:
    """Returns cached application settings instance."""
    return Settings()
