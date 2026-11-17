"""
OTP generation, delivery and verification for both channels.

Channel "sms" (target = 10-digit mobile number), delivery by settings.OTP_PROVIDER:
  static  -> no SMS; the OTP is always settings.OTP_STATIC_CODE (e.g. "1234")
  console -> random OTP written to the backend log; no SMS
  twilio  -> random OTP sent as a real SMS through the Twilio Messages API

Channel "email" (target = lower-cased email address), delivery by settings.EMAIL_OTP_PROVIDER:
  static  -> no email; the OTP is always settings.OTP_STATIC_CODE
  console -> random OTP written to the backend log; no email
  smtp    -> random OTP sent by email through the configured SMTP server (e.g. Gmail)

Generation and verification always happen here on the server, whatever the provider;
Twilio / SMTP only deliver the text. Each (channel, target, purpose) has its own OTP, so
email and SMS OTPs never interfere with each other.
"""

import asyncio
import hashlib
import hmac
import secrets
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import logger
from app.models.otp import OtpCode

SMS = "sms"
EMAIL = "email"
CHANNELS = (SMS, EMAIL)

TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

# Twilio error codes caused by the recipient number (or trial-account limits on it).
# These are shown to the user; any other Twilio error is a generic "could not send".
# https://www.twilio.com/docs/api/errors
TWILIO_RECIPIENT_ERRORS = {
    21211: "This mobile number is not valid. Please check it and try again.",
    21408: "SMS to this number's country is not enabled yet. Please contact support.",
    21608: "This number is not enabled to receive OTPs during testing. Please contact support.",
    21610: "This number has blocked SMS from us. Please contact support.",
    21614: "This number cannot receive SMS. Please use a mobile number.",
}


@dataclass
class OtpSent:
    """What the app needs to show after an OTP was sent."""

    channel: str
    destination: str  # masked phone / email
    otp_length: int
    expires_in_seconds: int
    resend_after_seconds: int
    delivered: bool = True  # False when no SMS/email was actually sent (static/console/test mode)
    test_code: str | None = None  # the fixed OTP to use when OTP_PROVIDER / EMAIL_OTP_PROVIDER = static


def _utc(dt: datetime) -> datetime:
    """MongoDB returns naive UTC datetimes; make them comparable with aware ones."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def mask(channel: str, target: str) -> str:
    if channel == SMS:
        return f"******{target[-4:]}"
    local, _, domain = target.partition("@")
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{'*' * max(1, len(local) - len(shown))}@{domain}"


def _hash(channel: str, target: str, purpose: str, code: str) -> str:
    return hmac.new(
        settings.JWT_SECRET.encode(), f"{channel}:{purpose}:{target}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def _test_codes(channel: str) -> dict[str, str]:
    """Fixed OTPs for store reviewers (OTP_TEST_NUMBERS / OTP_TEST_EMAILS)."""
    raw = settings.OTP_TEST_NUMBERS if channel == SMS else settings.OTP_TEST_EMAILS
    result: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" in pair:
            target, code = pair.rsplit(":", 1)
            target = target.strip()
            result[normalize_email(target) if channel == EMAIL else target] = code.strip()
    return result


def _provider(channel: str) -> str:
    return settings.OTP_PROVIDER if channel == SMS else settings.EMAIL_OTP_PROVIDER


def _generate_code(channel: str, target: str) -> str:
    fixed = _test_codes(channel).get(target)
    if fixed:
        return fixed
    if _provider(channel) == "static":
        return settings.OTP_STATIC_CODE
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


async def _deliver(channel: str, target: str, code: str) -> bool:
    """Send the OTP. Returns False when nothing was actually sent to the user."""
    provider = _provider(channel)
    if target in _test_codes(channel) or provider == "static":
        return False
    if provider == "console":
        logger.warning(f"[OTP console] {channel} OTP for {target}: {code}")
        return False
    if channel == SMS and provider == "twilio":
        await _send_twilio_sms(target, code)
        return True
    if channel == EMAIL and provider == "smtp":
        await _send_email(target, code)
        return True
    raise HTTPException(status_code=503, detail="OTP service is not configured.")


def _minutes() -> int:
    return max(1, settings.OTP_EXPIRE_SECONDS // 60)


# ─── SMS (Twilio) ─────────────────────────────────────────────────────────────


def _http_client() -> httpx.AsyncClient:
    """HTTP client for the SMS provider (replaced in tests)."""
    return httpx.AsyncClient(timeout=10)


def _sms_text(code: str) -> str:
    return settings.OTP_SMS_MESSAGE.replace("{code}", code).replace("{minutes}", str(_minutes()))


async def _send_twilio_sms(phone: str, code: str) -> None:
    """Send the OTP text through the Twilio Messages API."""
    sid, token = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
    if settings.TWILIO_MESSAGING_SERVICE_SID:
        sender = {"MessagingServiceSid": settings.TWILIO_MESSAGING_SERVICE_SID}
    elif settings.TWILIO_FROM_NUMBER:
        sender = {"From": settings.TWILIO_FROM_NUMBER}
    else:
        sender = None
    if not sid or not token or not sender:
        logger.error(
            "OTP_PROVIDER=twilio but TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / "
            "TWILIO_FROM_NUMBER (or TWILIO_MESSAGING_SERVICE_SID) are not all set"
        )
        raise HTTPException(status_code=503, detail="SMS OTP service is not configured.")

    masked = mask(SMS, phone)
    try:
        async with _http_client() as client:
            response = await client.post(
                TWILIO_MESSAGES_URL.format(sid=sid),
                auth=(sid, token),
                data={"To": f"{settings.OTP_SMS_COUNTRY_CODE}{phone}", "Body": _sms_text(code), **sender},
            )
    except httpx.HTTPError as exc:
        logger.error(f"Twilio request failed for {masked}: {type(exc).__name__}")
        raise HTTPException(status_code=502, detail="Could not send OTP SMS. Please try again.")

    if response.status_code < 400:
        return

    try:
        error = response.json()
    except ValueError:
        error = {}
    error_code = error.get("code")
    logger.error(
        f"Twilio send failed for {masked}: HTTP {response.status_code}, "
        f"code {error_code}: {error.get('message')}"
    )
    if error_code in TWILIO_RECIPIENT_ERRORS:
        raise HTTPException(status_code=400, detail=TWILIO_RECIPIENT_ERRORS[error_code])
    if response.status_code in (401, 403):
        # Wrong SID/token or a suspended account: our configuration, not the user's fault.
        raise HTTPException(status_code=503, detail="SMS OTP service is temporarily unavailable.")
    raise HTTPException(status_code=502, detail="Could not send OTP SMS. Please try again.")


# ─── Email (SMTP) ─────────────────────────────────────────────────────────────


def _smtp_connect() -> smtplib.SMTP:
    """Open an authenticated SMTP connection (replaced in tests)."""
    timeout = settings.SMTP_TIMEOUT_SECONDS
    if settings.SMTP_PORT == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout)
        server.starttls()
    # Gmail shows app passwords in groups of four ("abcd efgh ..."); the spaces are not part of it.
    server.login(settings.SMTP_USERNAME, (settings.SMTP_PASSWORD or "").replace(" ", ""))
    return server


def _email_message(email: str, code: str) -> EmailMessage:
    app, minutes = settings.SMTP_FROM_NAME, _minutes()
    msg = EmailMessage()
    msg["Subject"] = (
        settings.OTP_EMAIL_SUBJECT.replace("{code}", code).replace("{minutes}", str(minutes)).replace("{app}", app)
    )
    msg["From"] = formataddr((app, settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME))
    msg["To"] = email
    msg.set_content(
        f"Your {app} verification code is {code}.\n\n"
        f"It is valid for {minutes} minutes. Do not share it with anyone.\n"
        f"If you did not request this code, you can ignore this email."
    )
    msg.add_alternative(
        f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:auto;color:#111827">
  <h2 style="color:#2563EB;margin-bottom:4px">{app}</h2>
  <p>Your verification code is:</p>
  <p style="font-size:32px;font-weight:bold;letter-spacing:8px;margin:16px 0">{code}</p>
  <p>It is valid for {minutes} minutes. Do not share it with anyone.</p>
  <p style="color:#6B7280;font-size:13px">If you did not request this code, you can ignore this email.</p>
</div>""",
        subtype="html",
    )
    return msg


def _send_email_sync(email: str, code: str) -> None:
    server = _smtp_connect()
    try:
        server.send_message(_email_message(email, code))
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def _send_email(email: str, code: str) -> None:
    """Send the OTP email; map SMTP failures to user-facing errors."""
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        logger.error("EMAIL_OTP_PROVIDER=smtp but SMTP_USERNAME / SMTP_PASSWORD are not set")
        raise HTTPException(status_code=503, detail="Email OTP service is not configured.")

    masked = mask(EMAIL, email)
    try:
        await asyncio.to_thread(_send_email_sync, email, code)
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(f"SMTP login failed (check SMTP_USERNAME / SMTP_PASSWORD): {exc.smtp_code}")
        raise HTTPException(status_code=503, detail="Email OTP service is temporarily unavailable.")
    except smtplib.SMTPRecipientsRefused:
        logger.error(f"SMTP refused recipient {masked}")
        raise HTTPException(status_code=400, detail="This email address cannot receive email. Please check it.")
    except (smtplib.SMTPException, OSError) as exc:
        logger.error(f"SMTP send failed for {masked}: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Could not send OTP email. Please try again.")


# ─── Public API ───────────────────────────────────────────────────────────────


def _query(channel: str, target: str, purpose: str):
    return OtpCode.find(OtpCode.channel == channel, OtpCode.target == target, OtpCode.purpose == purpose)


async def send_otp(channel: str, target: str, purpose: str) -> OtpSent:
    """Create a new OTP for channel+target+purpose (replacing any earlier one) and deliver it."""
    if channel not in CHANNELS:
        raise HTTPException(status_code=400, detail="Unknown OTP channel.")
    now = datetime.now(timezone.utc)
    previous = await _query(channel, target, purpose).to_list()
    if previous:
        latest = max(_utc(p.created_at) for p in previous)
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - (now - latest).total_seconds()
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {int(wait) + 1} seconds before requesting another OTP.",
            )

    code = _generate_code(channel, target)
    delivered = await _deliver(channel, target, code)  # send first: if delivery fails, the old OTP stays usable

    await _query(channel, target, purpose).delete()
    await OtpCode(
        channel=channel,
        target=target,
        purpose=purpose,
        code_hash=_hash(channel, target, purpose, code),
        expires_at=now + timedelta(seconds=settings.OTP_EXPIRE_SECONDS),
        created_at=now,
    ).insert()
    return OtpSent(
        channel=channel,
        destination=mask(channel, target),
        otp_length=len(code),
        expires_in_seconds=settings.OTP_EXPIRE_SECONDS,
        resend_after_seconds=settings.OTP_RESEND_COOLDOWN_SECONDS,
        delivered=delivered,
        # Only the shared static code is revealed; reviewer test codes and console codes are not.
        test_code=code if _provider(channel) == "static" and target not in _test_codes(channel) else None,
    )


async def verify_otp(channel: str, target: str, purpose: str, code: str) -> None:
    """Raise HTTPException unless code is the current, unexpired OTP. A correct OTP is consumed."""
    otp = await OtpCode.find_one(
        OtpCode.channel == channel, OtpCode.target == target, OtpCode.purpose == purpose
    )
    if not otp or _utc(otp.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired or not requested. Please request a new OTP.")
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        await otp.delete()
        raise HTTPException(status_code=429, detail="Too many wrong attempts. Please request a new OTP.")
    if not hmac.compare_digest(otp.code_hash, _hash(channel, target, purpose, (code or "").strip())):
        otp.attempts += 1
        await otp.save()
        left = settings.OTP_MAX_ATTEMPTS - otp.attempts
        if left <= 0:
            await otp.delete()
            raise HTTPException(status_code=429, detail="Too many wrong attempts. Please request a new OTP.")
        raise HTTPException(status_code=400, detail=f"Invalid OTP. {left} attempt(s) left.")
    await otp.delete()  # single use
