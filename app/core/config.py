"""
Application configuration using Pydantic Settings.
All environment variables are validated at startup.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices
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

    # ─── OTP / SMS ────────────────────────────────────────────────────────────
    # static  -> no SMS is sent; every OTP is OTP_STATIC_CODE (current mode, "1234")
    # console -> random OTP, written to the backend log only (no SMS)
    # msg91   -> random OTP sent as a real SMS through MSG91 (needs DLT + keys below)
    OTP_PROVIDER: str = Field(default="static", description="static | console | msg91")
    OTP_STATIC_CODE: str = Field(default="1234", description="OTP used when OTP_PROVIDER=static")
    OTP_LENGTH: int = Field(default=4, ge=4, le=8, description="Digits in a generated OTP")
    OTP_EXPIRE_SECONDS: int = Field(default=300, ge=60, description="OTP validity")
    OTP_MAX_ATTEMPTS: int = Field(default=5, ge=1, description="Wrong tries before the OTP is discarded")
    OTP_RESEND_COOLDOWN_SECONDS: int = Field(default=30, ge=0, description="Min gap between OTP requests")
    OTP_TEST_NUMBERS: str = Field(
        default="", description="Fixed OTPs for store reviewers, e.g. 9999999999:4321,8888888888:8765"
    )
    MSG91_AUTH_KEY: Optional[str] = Field(default=None, description="MSG91 auth key")
    MSG91_TEMPLATE_ID: Optional[str] = Field(default=None, description="MSG91 flow/template id (DLT approved)")
    MSG91_OTP_VAR: str = Field(default="otp", description="Variable name of the OTP in the MSG91 template")

    # ─── Database ─────────────────────────────────────────────────────────────
    MONGODB_URI: str = Field(
        ...,
        validation_alias=AliasChoices("MONGODB_URI", "MONGO_URL"),
        description="MongoDB connection URI (supports Railway MONGO_URL)",
    )

    # ─── AI / LLM ─────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API key")
    AI_MODEL: str = Field(default="models/gemini-flash-lite-latest", description="Primary AI model")
    AI_VISION_MODEL: str = Field(
        default="models/gemini-flash-lite-latest",
        description="Vision/multimodal AI model",
    )

    AI_MAX_CONCURRENT_REQUESTS: int = Field(
        default=3, ge=1, le=20,
        description="Max simultaneous in-flight Gemini requests across the process",
    )

    # ─── Multi-Policy Analysis ────────────────────────────────────────────────
    MAX_POLICIES_PER_ANALYSIS: int = Field(
        default=5, ge=1, le=20,
        description="Max policies allowed in a single multi-policy analysis request",
    )
    MULTI_POLICY_MAX_CONCURRENCY: int = Field(
        default=2, ge=1, le=10,
        description="Max policy analyses processed concurrently within one multi-policy session",
    )

    # ─── Policy RAG / File Search ─────────────────────────────────────────────
    RAG_PROVIDER: str = Field(default="local", description="RAG search provider: local | gemini")
    RAG_TOP_K: int = Field(default=5, ge=1, le=20, description="Top K relevant clauses to retrieve")
    RAG_SIMILARITY_THRESHOLD: float = Field(default=0.15, ge=0.0, le=1.0, description="Minimum relevance score threshold")
    RAG_CACHE_ENABLED: bool = Field(default=True, description="Enable retrieval caching")
    RAG_CACHE_TTL_SECONDS: int = Field(default=3600, ge=60, description="Retrieval cache TTL in seconds")

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
        default="policies,prescriptions,analysisreports,analysissessions,multipolicyanalysissessions,analysisauditlogs,activitylogs,storedfiles",
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


settings = get_settings()
