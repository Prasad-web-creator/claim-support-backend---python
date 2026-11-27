"""
Public app configuration read by the mobile app at runtime.

Values come from environment variables, so local and production (Render) can differ
without an app release. No authentication: only non-secret values belong here.
"""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["App Config"])


@router.get("/app-config")
async def get_app_config() -> dict:
    return {
        "supportEmail": settings.SUPPORT_EMAIL.strip() or None,
        "maxFileSizeMb": settings.MAX_FILE_SIZE_MB,
        "maxFileSizeBytes": settings.max_file_size_bytes,
        "maxPoliciesPerAnalysis": settings.MAX_POLICIES_PER_ANALYSIS,
        "googleClientId": settings.GOOGLE_CLIENT_ID.strip() or None,
        "emailOtpEnabled": settings.EMAIL_OTP_PROVIDER != "disabled",
        "otpLength": settings.OTP_LENGTH,
        "analysisAgreementVersion": settings.ANALYSIS_AGREEMENT_VERSION,
    }
