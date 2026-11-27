"""Deliver OTP email through Gmail's send-only HTTPS API."""

import asyncio
import base64
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP
from time import monotonic

import httpx
from fastapi import HTTPException

from app.core.logging import logger

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
BEARER_TOKEN = re.compile(r"[a-zA-Z0-9._~+/-]+=*")


@dataclass(frozen=True)
class GmailCredentials:
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


@dataclass(frozen=True)
class GmailAccess:
    value: str = field(repr=False)
    expires_at: float
    credentials: GmailCredentials = field(repr=False)


class GmailDelivery:
    """Reuse a short-lived Google token and serialize token refreshes."""

    def __init__(self) -> None:
        self._access: GmailAccess | None = None
        self._refresh_lock = asyncio.Lock()

    async def _get_access(
        self, client: httpx.AsyncClient, credentials: GmailCredentials, rejected: GmailAccess | None = None
    ) -> GmailAccess:
        async with self._refresh_lock:
            cached = self._access
            if cached and cached is not rejected and cached.credentials == credentials and cached.expires_at > monotonic():
                return cached

            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "refresh_token": credentials.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if not response.is_success:
                logger.warning("Gmail OAuth token refresh failed (HTTP {})", response.status_code)
                raise HTTPException(status_code=503, detail="Email OTP service is temporarily unavailable.")
            try:
                data = response.json()
                token = data["access_token"]
                expires_in = data["expires_in"]
                if (
                    not isinstance(token, str)
                    or not BEARER_TOKEN.fullmatch(token)
                    or type(expires_in) is not int
                    or expires_in <= 0
                ):
                    raise ValueError("Invalid grant")
            except (KeyError, TypeError, ValueError):
                logger.warning("Gmail OAuth token response had an unexpected format")
                raise HTTPException(
                    status_code=503, detail="Email OTP service is temporarily unavailable."
                ) from None

            self._access = GmailAccess(token, monotonic() + max(0, expires_in - 60), credentials)
            return self._access

    async def send(self, client: httpx.AsyncClient, credentials: GmailCredentials, message: EmailMessage) -> None:
        raw = base64.urlsafe_b64encode(message.as_bytes(policy=SMTP)).decode("ascii")
        try:
            access = await self._get_access(client, credentials)
            response = await client.post(
                GMAIL_SEND_URL, headers={"Authorization": f"Bearer {access.value}"}, json={"raw": raw}
            )
            if response.status_code == 401:
                # A rejected authorization guarantees Gmail did not accept this attempt.
                access = await self._get_access(client, credentials, rejected=access)
                response = await client.post(
                    GMAIL_SEND_URL, headers={"Authorization": f"Bearer {access.value}"}, json={"raw": raw}
                )
        except httpx.HTTPError:
            logger.warning("Gmail API transport failed")
            raise HTTPException(status_code=502, detail="Could not send OTP email. Please try again.") from None

        if not response.is_success:
            logger.warning("Gmail API email delivery failed (HTTP {})", response.status_code)
            if response.status_code in (401, 403, 429):
                raise HTTPException(status_code=503, detail="Email OTP service is temporarily unavailable.")
            raise HTTPException(status_code=502, detail="Could not send OTP email. Please try again.")
