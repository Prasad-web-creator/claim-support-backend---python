"""
Application configuration using Pydantic Settings.
All environment variables are validated at startup.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ─── Application ──────────────────────────────────────────────────────────
    PORT: int = Field(default=8000, description="Server port")
    ENVIRONMENT: str = Field(default="development", description="development | production")

    # ─── Security ─────────────────────────────────────────────────────────────
    JWT_SECRET: str = Field(..., description="Secret key for JWT signing")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, description="Access token TTL")
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, description="Refresh token TTL")
    ALLOWED_ORIGINS: str = Field(default="", description="Comma-separated CORS origins")

    # ─── Database ─────────────────────────────────────────────────────────────
    MONGODB_URI: str = Field(..., description="MongoDB connection URI")

    # ─── AI / LLM ─────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API key")
    AI_MODEL: str = Field(default="models/gemini-2.5-flash", description="Primary AI model")
    AI_VISION_MODEL: str = Field(
        default="models/gemini-2.5-flash",
        description="Vision/multimodal AI model",
    )

    # ─── Logging ──────────────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(default="DEBUG", description="Logging level")

    # ─── Storage ──────────────────────────────────────────────────────────────
    UPLOAD_DIR: str = Field(default="uploads", description="Upload directory path")
    MAX_FILE_SIZE_MB: int = Field(default=20, description="Max upload file size in MB")
    ALLOWED_MIME_TYPES: str = Field(
        default="application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        description="Comma-separated allowed MIME types",
    )

    # ─── Data Retention ───────────────────────────────────────────────────────
    DATA_CLEANUP_DAYS: int = Field(default=30, ge=1, le=365, description="Days to retain non-permanent data")
    DATA_CLEANUP_DRY_RUN: bool = Field(default=False, description="Run cleanup without deleting")
    DATA_CLEANUP_MAX_PERCENTAGE: float = Field(default=0.05, ge=0.01, le=1.0, description="Max percentage to delete per run")
    DATA_CLEANUP_MAX_COUNT: int = Field(default=10000, description="Absolute maximum documents to delete per run")
    DATA_CLEANUP_COLLECTIONS_ALLOWLIST: str = Field(
        default="policies,prescriptions,analysisreports,analysissessions,analysisauditlogs,activitylogs,storedfiles",
        description="Comma-separated allowed collections for cleanup"
    )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def allowed_origins_list(self) -> list[str]:
        if not self.ALLOWED_ORIGINS:
            return ["http://localhost:8000"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_mime_types_list(self) -> list[str]:
        return [m.strip() for m in self.ALLOWED_MIME_TYPES.split(",") if m.strip()]

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def ttl_seconds(self) -> int:
        return self.DATA_CLEANUP_DAYS * 24 * 60 * 60

    @property
    def collections_allowlist(self) -> list[str]:
        return [c.strip() for c in self.DATA_CLEANUP_COLLECTIONS_ALLOWLIST.split(",") if c.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance — loaded once at startup."""
    return Settings()
