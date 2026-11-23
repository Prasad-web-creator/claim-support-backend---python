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
    }
