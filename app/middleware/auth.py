"""
JWT Authentication middleware — migrated from authMiddleware.js.
FastAPI dependency for route-level authentication.
"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError

from app.core.security import verify_access_token

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """
    FastAPI dependency that extracts and verifies JWT from Authorization header.
    Returns the user dict with 'id' field.
    """
    if credentials is None:
        # Also check raw header for backward compatibility with mobile clients
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            raise HTTPException(status_code=401, detail="No token, authorization denied")
        parts = auth_header.split(" ")
        token = parts[1] if len(parts) > 1 else parts[0]
    else:
        token = credentials.credentials

    try:
        payload = verify_access_token(token)
        user = payload.get("user")
        if not user or not user.get("id"):
            raise HTTPException(status_code=401, detail="Token is not valid")
        return user
    except JWTError as e:
        if "expired" in str(e).lower():
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Token is not valid")
