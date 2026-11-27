"""Verify Google OpenID Connect ID tokens against this application's client ID."""

import asyncio
from dataclasses import dataclass
from functools import lru_cache, partial
from threading import Lock

import requests
from cachecontrol import CacheControl
from fastapi import HTTPException
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import settings
from app.services.otp_service import normalize_email

_request_lock = Lock()
_email_adapter = TypeAdapter(EmailStr)


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str
    authoritative_email: bool


@lru_cache(maxsize=1)
def _cert_request() -> Request:
    return Request(session=CacheControl(requests.Session()))


def _fetch_certs(*args, **kwargs):
    # Cache-control headers set Google public-key expiry. Serialize access to the shared session.
    with _request_lock:
        return _cert_request()(*args, **kwargs)


async def verify_google_token(token: str) -> GoogleIdentity:
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google Sign-In is not configured.")
    invalid = HTTPException(status_code=401, detail="Google Sign-In could not be verified.")
    try:
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            token,
            partial(_fetch_certs, timeout=settings.AUTH_REQUEST_TIMEOUT_SECONDS),
            audience=settings.GOOGLE_CLIENT_ID,
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
            raise invalid
        if claims.get("email_verified") is not True:
            raise invalid
        email = normalize_email(_email_adapter.validate_python(claims.get("email")))
    except TransportError:
        raise HTTPException(status_code=503, detail="Google Sign-In is temporarily unavailable.") from None
    except (GoogleAuthError, ValidationError, ValueError, TypeError, KeyError):
        raise invalid from None

    name = claims.get("name")
    clean_name = name.strip()[:100] if isinstance(name, str) else ""
    hosted_domain = claims.get("hd")
    return GoogleIdentity(
        subject=subject,
        email=email,
        name=clean_name or email.split("@", 1)[0][:100],
        authoritative_email=email.endswith("@gmail.com") or (
            isinstance(hosted_domain, str) and bool(hosted_domain.strip())
        ),
    )
