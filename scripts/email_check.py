"""
Check the configured email OTP provider in .env.

    python -m scripts.email_check                      # SMTP connection check
    python -m scripts.email_check someone@example.com  # sends a one-time delivery check

Omit the recipient to check SMTP authentication without sending a message.
"""

import asyncio
import smtplib
import sys

from app.core.config import settings
import httpx

from app.services.microsoft_smtp import MicrosoftTokenError
from app.services.otp_service import EMAIL, _deliver, _generate_code, _smtp_access_token, _smtp_connect, normalize_email


async def main() -> int:
    if len(sys.argv) > 1:
        try:
            await _deliver(EMAIL, normalize_email(sys.argv[1]), _generate_code())
        except Exception as exc:
            print(f"Email delivery check failed: {getattr(exc, 'status_code', type(exc).__name__)}")
            return 1
        print("Test email sent. The code was generated only for this delivery check.")
        return 0

    if settings.EMAIL_OTP_PROVIDER not in ("smtp", "smtp_oauth"):
        print("Supply an email address to run a delivery check with the configured provider.")
        return 1
    print(f"Connecting to {settings.SMTP_HOST}:{settings.SMTP_PORT} ...")
    try:
        token = await _smtp_access_token()
        if token is not None:
            print("Microsoft Entra issued an SMTP access token.")
        server = await asyncio.to_thread(_smtp_connect, token)
        await asyncio.to_thread(server.quit)
    except MicrosoftTokenError as exc:
        print(f"Microsoft Entra rejected the token request ({exc}).")
        return 1
    except httpx.HTTPError as exc:
        print(f"Could not reach Microsoft Entra ({type(exc).__name__}).")
        return 1
    except smtplib.SMTPAuthenticationError as exc:
        print(f"SMTP authentication rejected ({exc.smtp_code}). Check the configured mailbox and its permissions.")
        return 1
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Could not connect: {type(exc).__name__}")
        return 1
    print("Login OK.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
