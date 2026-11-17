"""
Check the SMTP settings in .env before switching EMAIL_OTP_PROVIDER to smtp.

    python -m scripts.email_check                      # logs in to the SMTP server only, sends nothing
    python -m scripts.email_check someone@example.com  # also sends one test OTP email (code 0000)

For Gmail, SMTP_PASSWORD must be an App Password (Google Account > Security >
2-Step Verification > App passwords), not the normal account password.
"""

import asyncio
import smtplib
import sys

from app.core.config import settings
from app.services.otp_service import _send_email, _smtp_connect


async def main() -> int:
    missing = [n for n, v in [("SMTP_USERNAME", settings.SMTP_USERNAME), ("SMTP_PASSWORD", settings.SMTP_PASSWORD)] if not v]
    if missing:
        print("Missing in .env:", ", ".join(missing))
        return 1

    print(f"Connecting to {settings.SMTP_HOST}:{settings.SMTP_PORT} as {settings.SMTP_USERNAME} ...")
    try:
        server = await asyncio.to_thread(_smtp_connect)
        await asyncio.to_thread(server.quit)
    except smtplib.SMTPAuthenticationError as exc:
        print(f"Login rejected ({exc.smtp_code}). Check SMTP_USERNAME / SMTP_PASSWORD (Gmail needs an App Password).")
        return 1
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Could not connect: {type(exc).__name__}: {exc}")
        return 1
    print("Login OK.")
    print(f"Sender: {settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME}>")
    print(f"EMAIL_OTP_PROVIDER is currently: {settings.EMAIL_OTP_PROVIDER}")

    if len(sys.argv) > 1:
        try:
            await _send_email(sys.argv[1].strip().lower(), "0000")
        except Exception as exc:  # HTTPException carries the user-facing reason
            print(f"Send failed: {getattr(exc, 'detail', exc)}")
            return 1
        print(f"Test email sent to {sys.argv[1]}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
