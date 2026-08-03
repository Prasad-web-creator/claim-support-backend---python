"""
JWT token management and security utilities.
Replaces jsonwebtoken from Node.js.
"""

import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def create_access_token(user_id: str) -> str:
    """Create a JWT access token with the user ID embedded."""
    settings = get_settings()
    payload = {
        "user": {"id": user_id},
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def verify_access_token(token: str) -> dict:
    """
    Verify and decode a JWT access token.
    Returns the decoded payload.
    Raises JWTError on invalid/expired tokens.
    """
    settings = get_settings()
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])


def generate_refresh_token() -> str:
    """Generate a cryptographically secure refresh token."""
    return secrets.token_hex(40)


def get_refresh_token_expiry() -> datetime:
    """Calculate refresh token expiry date."""
    settings = get_settings()
    return datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
