"""Microsoft Entra client credentials for Exchange Online SMTP OAuth."""

import asyncio
import re
from time import monotonic

import httpx

from app.core.config import Settings


_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9._~+/-]+=*")


class MicrosoftTokenError(Exception):
    """Report a token endpoint failure without leaking any credentials."""


class MicrosoftSmtpTokens:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._token = ""
        self._expires_at = 0.0
        self._credentials: tuple[str, str, str] | None = None

    async def get(self, config: Settings, client: httpx.AsyncClient) -> str:
        async with self._lock:
            credentials = (config.SMTP_TENANT_ID, config.SMTP_CLIENT_ID, config.SMTP_CLIENT_SECRET)
            if self._token and credentials == self._credentials and monotonic() < self._expires_at:
                return self._token
            response = await client.post(
                f"https://login.microsoftonline.com/{config.SMTP_TENANT_ID}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": config.SMTP_CLIENT_ID,
                    "client_secret": config.SMTP_CLIENT_SECRET,
                    "scope": "https://outlook.office365.com/.default",
                },
            )
            if not response.is_success:
                # Limit diagnostics to Microsoft's numeric error identifiers.
                try:
                    codes = response.json()["error_codes"]
                    codes = [code for code in codes if type(code) is int][:3] if isinstance(codes, list) else []
                except (ValueError, KeyError, TypeError):
                    codes = []
                raise MicrosoftTokenError(f"HTTP {response.status_code}, Entra codes {codes}")
            try:
                data = response.json()
                token, seconds = data["access_token"], data["expires_in"]
                if not isinstance(token, str) or not _TOKEN_FORMAT.fullmatch(token):
                    raise ValueError
                if type(seconds) is not int or seconds <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise MicrosoftTokenError("Invalid token response") from None
            self._token = token
            self._credentials = credentials
            self._expires_at = monotonic() + max(0, seconds - 60)
            return token
