"""OTP generation, single-use verification and delivery through external providers."""

import asyncio
import hashlib
import hmac
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

import httpx
from bson import ObjectId
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.language import Language
from app.core.logging import logger
from app.models.otp import OtpCode
from app.services.gmail_delivery import GmailCredentials, GmailDelivery
from app.services.microsoft_smtp import MicrosoftSmtpTokens, MicrosoftTokenError

EMAIL = "email"

RESEND_EMAILS_URL = "https://api.resend.com/emails"
_gmail_delivery = GmailDelivery()
_microsoft_tokens = MicrosoftSmtpTokens()


@dataclass
class OtpSent:
    """What the app needs to show after an OTP was sent."""

    destination: str  # masked email
    otp_length: int
    expires_in_seconds: int
    resend_after_seconds: int


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def mask_email(target: str) -> str:
    local, _, domain = target.partition("@")
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{'*' * max(1, len(local) - len(shown))}@{domain}"


def _hash(challenge_id: ObjectId, generation: str, code: str) -> str:
    return hmac.new(
        (settings.OTP_HASH_SECRET or settings.JWT_SECRET).encode(),
        f"otp:v2:{challenge_id}:{generation}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _generate_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


async def _deliver(target: str, code: str, language: Language = "en") -> None:
    provider = settings.EMAIL_OTP_PROVIDER
    if provider in ("smtp", "smtp_oauth"):
        await _send_email(target, code, language)
    elif provider == "resend":
        await _send_resend_email(target, code, language)
    elif provider == "gmail":
        credentials = GmailCredentials(
            settings.GMAIL_CLIENT_ID, settings.GMAIL_CLIENT_SECRET, settings.GMAIL_REFRESH_TOKEN
        )
        async with _http_client(trust_env=False) as client:
            await _gmail_delivery.send(client, credentials, _email_message(target, code, settings.GMAIL_FROM_ADDRESS, language))
    else:
        raise HTTPException(status_code=503, detail="OTP service is not configured.")


def _minutes() -> int:
    return max(1, settings.OTP_EXPIRE_SECONDS // 60)


def _http_client(*, trust_env: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.AUTH_REQUEST_TIMEOUT_SECONDS, trust_env=trust_env)


# ─── Email (SMTP) ─────────────────────────────────────────────────────────────


def _smtp_connect(access_token: str | None = None) -> smtplib.SMTP:
    """Open an authenticated SMTP connection (replaced in tests)."""
    timeout = settings.SMTP_TIMEOUT_SECONDS
    tls_context = ssl.create_default_context()
    if settings.SMTP_PORT == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout, context=tls_context
        )
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=timeout)
    try:
        if settings.SMTP_PORT != 465:
            server.starttls(context=tls_context)
        if access_token is None:
            server.login(settings.SMTP_USERNAME, (settings.SMTP_PASSWORD or "").replace(" ", ""))
        else:
            server.ehlo_or_helo_if_needed()
            server.auth(
                "XOAUTH2",
                lambda challenge=None: f"user={settings.SMTP_USERNAME}\x01auth=Bearer {access_token}\x01\x01"
                if challenge is None else "",
            )
    except Exception:
        server.close()
        raise
    return server


async def _smtp_access_token() -> str | None:
    if settings.EMAIL_OTP_PROVIDER != "smtp_oauth":
        return None
    async with _http_client(trust_env=False) as client:
        return await _microsoft_tokens.get(settings, client)


def _email_message(email: str, code: str, sender: str | None = None, language: Language = "en") -> EmailMessage:
    app, minutes = settings.SMTP_FROM_NAME, _minutes()
    msg = EmailMessage()
    if language == "ta":
        msg["Subject"] = f"{app} சரிபார்ப்புக் குறியீடு"
        intro = f"உங்கள் {app} சரிபார்ப்புக் குறியீடு {code}."
        code_label = "உங்கள் சரிபார்ப்புக் குறியீடு:"
        expiry = f"இந்தக் குறியீடு {minutes} நிமிடங்களுக்குச் செல்லும். இதை யாருடனும் பகிர வேண்டாம்."
        unrequested = "இந்தக் குறியீட்டை நீங்கள் கோரவில்லை என்றால், இந்த மின்னஞ்சலைப் புறக்கணிக்கலாம்."
    else:
        msg["Subject"] = settings.OTP_EMAIL_SUBJECT.replace("{code}", code).replace("{minutes}", str(minutes)).replace("{app}", app)
        intro = f"Your {app} verification code is {code}."
        code_label = "Your verification code is:"
        expiry = f"It is valid for {minutes} minutes. Do not share it with anyone."
        unrequested = "If you did not request this code, you can ignore this email."
    msg["From"] = formataddr((app, sender or settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME))
    msg["To"] = email
    msg.set_content(f"{intro}\n\n{expiry}\n{unrequested}")
    msg.add_alternative(
        f"""<div lang="{language}" style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:auto;color:#111827">
  <h2 style="color:#2563EB;margin-bottom:4px">{escape(app)}</h2>
  <p>{code_label}</p>
  <p style="font-size:32px;font-weight:bold;letter-spacing:8px;margin:16px 0">{code}</p>
  <p>{expiry}</p>
  <p style="color:#6B7280;font-size:13px">{unrequested}</p>
</div>""",
        subtype="html",
    )
    return msg


def _send_email_sync(email: str, code: str, token: str | None = None, language: Language = "en") -> None:
    server = _smtp_connect(token) if token is not None else _smtp_connect()
    try:
        server.send_message(_email_message(email, code, language=language))
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def _send_email(email: str, code: str, language: Language = "en") -> None:
    """Send the OTP email; map SMTP failures to user-facing errors."""
    if settings.EMAIL_OTP_PROVIDER == "smtp" and not (settings.SMTP_USERNAME and settings.SMTP_PASSWORD):
        raise HTTPException(status_code=503, detail="Email OTP service is not configured.")
    try:
        token = await _smtp_access_token()
        await asyncio.to_thread(_send_email_sync, email, code, token, language)
    except (MicrosoftTokenError, httpx.HTTPError) as exc:
        detail = str(exc) if isinstance(exc, MicrosoftTokenError) else type(exc).__name__
        logger.warning("Microsoft SMTP token request failed ({})", detail)
        raise HTTPException(status_code=503, detail="Email OTP service is temporarily unavailable.") from None
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication rejected ({})", exc.smtp_code)
        raise HTTPException(status_code=503, detail="Email OTP service is temporarily unavailable.")
    except smtplib.SMTPRecipientsRefused:
        logger.error("SMTP refused an OTP recipient")
        raise HTTPException(status_code=400, detail="This email address cannot receive email. Please check it.")
    except (smtplib.SMTPException, OSError) as exc:
        logger.error(f"SMTP OTP send failed: {type(exc).__name__}")
        raise HTTPException(status_code=502, detail="Could not send OTP email. Please try again.")


async def _send_resend_email(email: str, code: str, language: Language = "en") -> None:
    if not settings.EMAIL_API_KEY or not settings.EMAIL_FROM_ADDRESS:
        raise HTTPException(status_code=503, detail="Email OTP service is not configured.")
    message = _email_message(email, code, settings.EMAIL_FROM_ADDRESS, language)
    try:
        async with _http_client() as client:
            response = await client.post(
                RESEND_EMAILS_URL,
                headers={"Authorization": f"Bearer {settings.EMAIL_API_KEY}"},
                json={
                    "from": str(message["From"]),
                    "to": [email],
                    "subject": str(message["Subject"]),
                    "text": message.get_body(("plain",)).get_content(),
                    "html": message.get_body(("html",)).get_content(),
                },
            )
    except httpx.HTTPError:
        logger.warning("Resend OTP request failed")
        raise HTTPException(status_code=502, detail="Could not send OTP email. Please try again.") from None
    if response.is_success:
        return
    logger.warning(f"Resend OTP send failed: HTTP {response.status_code}")
    if response.status_code in (401, 403, 429):
        raise HTTPException(status_code=503, detail="Email OTP service is temporarily unavailable.")
    raise HTTPException(status_code=502, detail="Could not send OTP email. Please try again.")


# ─── Public API ───────────────────────────────────────────────────────────────


def _challenge_id(target: str, purpose: str) -> ObjectId:
    # Keep the email namespace so in-flight email challenges survive a deployment.
    digest = hashlib.sha256(f"otp:v2:{EMAIL}:{purpose}:{target}".encode()).digest()
    return ObjectId(digest[:12])


async def send_otp(target: str, purpose: str, language: Language = "en") -> OtpSent:
    """Reserve a challenge before delivery so overlapping sends cannot bypass the cooldown."""
    now = datetime.now(timezone.utc)
    cooldown = settings.OTP_RESEND_COOLDOWN_SECONDS
    challenge_id = _challenge_id(target, purpose)
    generation = secrets.token_hex(16)
    code = _generate_code()
    valid_until = now + timedelta(seconds=settings.OTP_EXPIRE_SECONDS)
    current = {"_id": challenge_id, "generation": generation}
    collection = OtpCode.get_motor_collection()
    try:
        previous = await collection.find_one_and_update(
            {
                "_id": challenge_id,
                "$or": [
                    {"state": {"$ne": "pending"}, "created_at": {"$lte": now - timedelta(seconds=cooldown)}},
                    {"expires_at": {"$lte": now}},
                ],
            },
            {"$set": {
                "channel": EMAIL, "target": target, "purpose": purpose, "generation": generation,
                "state": "pending", "code_hash": _hash(challenge_id, generation, code),
                "attempts": 0, "created_at": now, "valid_until": valid_until,
                "expires_at": max(valid_until, now + timedelta(seconds=cooldown)),
            }},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
    except DuplicateKeyError:
        raise HTTPException(
            status_code=429, detail="Please wait before requesting another OTP."
        ) from None

    try:
        await _deliver(target, code, language)
    except (Exception, asyncio.CancelledError):
        if previous is None:
            await collection.delete_one(current)
        else:
            await collection.replace_one(current, previous)
        raise

    activated = await collection.update_one(current, {"$set": {"state": "active"}})
    if not activated.matched_count:
        raise HTTPException(status_code=503, detail="Could not send OTP. Please try again.")
    return OtpSent(
        destination=mask_email(target),
        otp_length=len(code),
        expires_in_seconds=settings.OTP_EXPIRE_SECONDS,
        resend_after_seconds=cooldown,
    )


async def verify_otp(target: str, purpose: str, code: str) -> None:
    """Consume a correct code once or atomically debit an incorrect attempt."""
    challenge_id = _challenge_id(target, purpose)
    collection = OtpCode.get_motor_collection()
    invalid = HTTPException(status_code=400, detail="Invalid or expired OTP. Please request a new code if needed.")
    active = {
        "_id": challenge_id,
        "state": "active",
        "valid_until": {"$gt": datetime.now(timezone.utc)},
        "attempts": {"$lt": settings.OTP_MAX_ATTEMPTS},
    }
    otp = await collection.find_one(active, {"generation": 1, "code_hash": 1})
    if otp is None:
        raise invalid
    active["generation"] = otp["generation"]
    candidate = _hash(challenge_id, otp["generation"], code.strip())
    if hmac.compare_digest(otp["code_hash"], candidate):
        consumed = await collection.update_one(active, {"$set": {"state": "used"}, "$unset": {"code_hash": ""}})
        if consumed.matched_count:
            return
    else:
        await collection.update_one(active, {"$inc": {"attempts": 1}})
    raise invalid
