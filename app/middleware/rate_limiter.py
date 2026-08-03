"""
Rate limiting middleware using slowapi.
Replaces express-rate-limit.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Global rate limiter instance
limiter = Limiter(key_func=get_remote_address)


def get_user_key(request) -> str:
    """Extract user ID from request state for user-based rate limiting."""
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict):
        return user.get("id", get_remote_address(request))
    return get_remote_address(request)
