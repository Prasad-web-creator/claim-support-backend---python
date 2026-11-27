"""
Application configuration using Pydantic Settings.
All environment variables are validated at startup.
"""

from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices, field_validator, model_validator
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ─── Application ──────────────────────────────────────────────────────────
    PORT: int = Field(default=8000, description="Server port")
    ENVIRONMENT: str = Field(default="development", description="development | production")
    SUPPORT_EMAIL: str = Field(
        default="", description="Help & Support contact shown in the app (served by GET /api/app-config)"
    )

    # ─── Security ─────────────────────────────────────────────────────────────
    JWT_SECRET: str = Field(..., min_length=1, repr=False, description="Secret key for JWT signing")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=1, description="Access token TTL")
    # Sliding: every refresh issues a new refresh token with a fresh TTL, so a user
    # stays signed in until they log out unless the app is unused for this long.
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=365, ge=1, description="Refresh token TTL (days)")
    ALLOWED_ORIGINS: str = Field(default="", description="Comma-separated CORS origins")

    # ─── Authentication ──────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = Field(default="", description="Public Google Web OAuth client ID")
    OTP_LENGTH: int = Field(default=6, ge=6, le=8, description="Digits in a generated OTP")
    OTP_EXPIRE_SECONDS: int = Field(default=300, ge=60, description="OTP validity")
    OTP_MAX_ATTEMPTS: int = Field(default=5, ge=1, description="Wrong tries before the OTP is discarded")
    OTP_RESEND_COOLDOWN_SECONDS: int = Field(default=60, ge=1, description="Min gap between OTP requests")
    OTP_HASH_SECRET: str = Field(default="", description="Separate OTP signing secret, defaults to JWT_SECRET")
    OTP_SEND_RATE_LIMIT: str = Field(default="5/minute", description="Per-IP OTP send rate limit")
    OTP_VERIFY_RATE_LIMIT: str = Field(default="10/minute", description="Per-IP OTP and sign-in verification rate limit")
    AUTH_REQUEST_TIMEOUT_SECONDS: int = Field(default=10, ge=1, le=30, description="Authentication network timeout")

    # ─── Email OTP ────────────────────────────────────────────────────────────
    EMAIL_OTP_PROVIDER: str = Field(
        default="disabled", pattern=r"^(disabled|smtp|smtp_oauth|resend|gmail)$",
        description="disabled | smtp | smtp_oauth | resend | gmail",
    )
    EMAIL_API_KEY: str = Field(default="", description="Resend sending API key")
    EMAIL_FROM_ADDRESS: str = Field(default="", description="Resend verified sender address")
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = Field(default="", repr=False)
    GMAIL_REFRESH_TOKEN: str = Field(default="", repr=False)
    GMAIL_FROM_ADDRESS: str = ""
    OTP_EMAIL_SUBJECT: str = Field(
        default="Your {app} verification code",
        description="Email subject; {code}, {minutes} and {app} are filled in",
    )
    SMTP_HOST: str = Field(default="smtp.gmail.com", description="SMTP server host")
    SMTP_PORT: int = Field(default=587, description="SMTP port (587 = STARTTLS, 465 = SSL)")
    SMTP_USERNAME: Optional[str] = Field(default=None, description="SMTP login (Gmail address)")
    SMTP_PASSWORD: Optional[str] = Field(default=None, description="SMTP password (Gmail app password)")
    SMTP_FROM_EMAIL: Optional[str] = Field(default=None, description="Sender address; defaults to SMTP_USERNAME")
    SMTP_FROM_NAME: str = Field(default="Policy Pal", description="Sender display name / app name in emails")
    SMTP_TIMEOUT_SECONDS: int = Field(default=15, ge=1, description="SMTP connect/send timeout")
    SMTP_TENANT_ID: str = Field(default="", validation_alias=AliasChoices("SMTP_TENANT_ID", "TENANT_ID"))
    SMTP_CLIENT_ID: str = Field(default="", validation_alias=AliasChoices("SMTP_CLIENT_ID", "CLIENT_ID"))
    SMTP_CLIENT_SECRET: str = Field(
        default="", repr=False, validation_alias=AliasChoices("SMTP_CLIENT_SECRET", "CLIENT_SECRET"),
    )

    # ─── Database ─────────────────────────────────────────────────────────────
    MONGODB_URI: str = Field(
        ...,
        validation_alias=AliasChoices("MONGODB_URI", "MONGO_URL"),
        repr=False,
        description="MongoDB connection URI (supports Railway MONGO_URL)",
    )
    MONGODB_DATABASE: Optional[str] = Field(default=None, description="Database override, otherwise URI database or claimsupport")

    # ─── AI / LLM ─────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = Field(default="", repr=False, description="Google Gemini API key required to start the API")
    REQUIRE_ANALYSIS_CONSENT: bool = True
    ANALYSIS_AGREEMENT_VERSION: str = Field(default="1", min_length=1, max_length=40)
    ANALYSIS_JOB_EXPIRY_HOURS: int = Field(default=72, ge=1, le=720)
    AI_MODEL: str = Field(default="models/gemini-flash-lite-latest", description="Primary AI model")
    AI_MAX_ATTEMPTS: int = Field(
        default=3, ge=1,
        description="Total attempts per LLM generation request, including the first call",
    )

    AI_MAX_CONCURRENT_REQUESTS: int = Field(
        default=3, ge=1, le=20,
        description="Max simultaneous in-flight Gemini requests across the process",
    )

    # ─── Multi-Policy Analysis ────────────────────────────────────────────────
    MAX_POLICIES_PER_ANALYSIS: int = Field(
        default=3, ge=1, le=5,
        description="Max policies allowed in a single multi-policy analysis request",
    )
    MULTI_POLICY_MAX_CONCURRENCY: int = Field(
        default=2, ge=1, le=10,
        description="Max policy analyses processed concurrently within one multi-policy session",
    )

    # ─── Logging ──────────────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(default="INFO", pattern=r"^(TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)$", description="Logging level")
    SENTRY_ENABLED: bool = Field(default=False, description="Enable privacy-filtered backend error tracking")
    SENTRY_DSN: str = Field(default="", description="Sentry project DSN")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0, ge=0, le=1, description="Trace sampling fraction")
    SENTRY_ERROR_SAMPLE_RATE: float = Field(default=1, ge=0, le=1, description="Error sampling fraction")

    # ─── Storage ──────────────────────────────────────────────────────────────
    MAX_FILE_SIZE_MB: int = Field(default=5, ge=1, le=10, description="Max upload file size in MiB")
    ALLOWED_MIME_TYPES: str = Field(
        default="application/pdf,image/png,image/jpeg,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        description="Comma-separated allowed MIME types",
    )

    # ─── Data Retention ───────────────────────────────────────────────────────
    PDF_COMPRESSION_ENABLED: bool = True
    PDF_COMPRESSION_DPI: int = Field(default=150, ge=72, le=300)
    PDF_GRAYSCALE_ENABLED: bool = True
    PDF_COMPRESSION_JPEG_QUALITY: int = Field(default=85, ge=70, le=95)
    PDF_COMPRESSION_MAX_PAGE_PIXELS: int = Field(default=10_000_000, ge=1_000_000, le=25_000_000)
    PDF_COMPRESSION_MAX_PAGES: int = Field(default=100, ge=1, le=1000)
    FILE_DEDUPLICATION_ENABLED: bool = True
    ORPHAN_CLEANUP_ENABLED: bool = True
    DOCUMENT_RETENTION_DAYS: int = Field(default=0, ge=0, le=3650, description="Delete old inactive uploaded records via the ownership-aware cascade. Zero keeps documents.")
    ORPHAN_CLEANUP_INTERVAL_HOURS: int = Field(default=24, ge=1, le=168)
    ORPHAN_CLEANUP_GRACE_HOURS: int = Field(default=24, ge=1, le=720)
    MAINTENANCE_CHECK_INTERVAL_MINUTES: int = Field(default=15, ge=1, le=60)
    STORAGE_MONITOR_ENABLED: bool = True
    STORAGE_MONITOR_INTERVAL_HOURS: int = Field(default=24, ge=1, le=168)
    STORAGE_CAPACITY_BYTES: int = Field(default=536870912, gt=0)
    STORAGE_WARNING_THRESHOLD_PERCENT: int = Field(default=80, ge=1, le=100)
    STORAGE_CRITICAL_THRESHOLD_PERCENT: int = Field(default=90, ge=1, le=100)
    DATA_CLEANUP_DRY_RUN: bool = Field(default=False, description="Run cleanup without deleting")
    DATA_CLEANUP_MAX_PERCENTAGE: float = Field(default=0.05, ge=0.01, le=1.0, description="Max percentage to delete per run")
    DATA_CLEANUP_MAX_COUNT: int = Field(default=10000, ge=1, description="Maximum orphan candidates per run")

    OCR_DPI: int = Field(default=150, ge=72, le=300)
    OCR_LANGUAGE: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*$")
    OCR_PREPROCESS_ENABLED: bool = True
    OCR_DESKEW_ENABLED: bool = True
    OCR_THRESHOLD_ENABLED: bool = False
    OCR_MIN_CONFIDENCE: float = Field(default=50, ge=0, le=100)
    OCR_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=300)
    OCR_QUEUE_TIMEOUT_SECONDS: int = Field(default=15, ge=1, le=60)
    OCR_MAX_SCANNED_PAGES: int = Field(default=0, ge=0, le=200, description="Optional explicit scan limit. Zero means process all pages within the timeout.")
    OCR_MAX_IMAGE_PIXELS: int = Field(default=8_000_000, ge=1_000_000, le=20_000_000)
    TESSERACT_CMD: str = Field(default="tesseract", min_length=1)
    TESSDATA_PREFIX: str = Field(default="", description="Optional path to installed Tesseract language data")

    @field_validator("EMAIL_OTP_PROVIDER", mode="before")
    @classmethod
    def disable_insecure_legacy_providers(cls, value):
        return "disabled" if value in ("static", "console") else value

    @model_validator(mode="after")
    def validate_service_configuration(self):
        from limits import parse

        parse(self.OTP_SEND_RATE_LIMIT)
        parse(self.OTP_VERIFY_RATE_LIMIT)
        if self.STORAGE_WARNING_THRESHOLD_PERCENT >= self.STORAGE_CRITICAL_THRESHOLD_PERCENT:
            raise ValueError("Storage warning threshold must be below the critical threshold")
        if self.SENTRY_ENABLED and not self.SENTRY_DSN:
            raise ValueError("SENTRY_DSN is required when Sentry is enabled")
        if self.EMAIL_OTP_PROVIDER == "resend" and not (self.EMAIL_API_KEY and self.EMAIL_FROM_ADDRESS):
            raise ValueError("Resend requires EMAIL_API_KEY and EMAIL_FROM_ADDRESS")
        if self.EMAIL_OTP_PROVIDER == "smtp" and not (self.SMTP_USERNAME and self.SMTP_PASSWORD):
            raise ValueError("SMTP requires SMTP_USERNAME and SMTP_PASSWORD")
        if self.EMAIL_OTP_PROVIDER == "smtp_oauth":
            from uuid import UUID
            from email_validator import validate_email

            if self.SMTP_HOST.lower() != "smtp.office365.com" or self.SMTP_PORT != 587:
                raise ValueError("Microsoft 365 SMTP OAuth requires smtp.office365.com on port 587")
            if not all((self.SMTP_TENANT_ID, self.SMTP_CLIENT_ID, self.SMTP_CLIENT_SECRET, self.SMTP_USERNAME)):
                raise ValueError("Microsoft 365 SMTP OAuth requires a tenant ID, client ID, client secret and mailbox")
            self.SMTP_TENANT_ID = str(UUID(self.SMTP_TENANT_ID))
            self.SMTP_CLIENT_ID = str(UUID(self.SMTP_CLIENT_ID))
            self.SMTP_USERNAME = validate_email(
                self.SMTP_USERNAME, check_deliverability=False, allow_smtputf8=False,
            ).normalized
            if self.SMTP_FROM_EMAIL:
                self.SMTP_FROM_EMAIL = validate_email(self.SMTP_FROM_EMAIL, check_deliverability=False).normalized
        if self.EMAIL_OTP_PROVIDER == "gmail":
            from email_validator import validate_email
            if not all((self.GMAIL_CLIENT_ID, self.GMAIL_CLIENT_SECRET, self.GMAIL_REFRESH_TOKEN, self.GMAIL_FROM_ADDRESS)):
                raise ValueError("Gmail requires client ID, client secret, refresh token and sender")
            validate_email(self.GMAIL_FROM_ADDRESS, check_deliverability=False)
        return self

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore", "hide_input_in_errors": True}


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance — loaded once at startup."""
    return Settings()


settings = get_settings()
