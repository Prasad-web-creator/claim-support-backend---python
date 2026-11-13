"""
OTP generation, delivery and verification.

Delivery depends on settings.OTP_PROVIDER:
  static  -> no SMS; the OTP is always settings.OTP_STATIC_CODE (e.g. "1234")
  console -> random OTP written to the backend log; no SMS
  msg91   -> random OTP sent as a real SMS through MSG91
Verification always happens here on the server, whatever the provider.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import logger
from app.models.otp import OtpCode

MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow/"


def _utc(dt: datetime) -> datetime:
    """MongoDB returns naive UTC datetimes; make them comparable with aware ones."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hash(phone: str, purpose: str, code: str) -> str:
    return hmac.new(
        settings.JWT_SECRET.encode(), f"{purpose}:{phone}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def _test_numbers() -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in settings.OTP_TEST_NUMBERS.split(","):
        if ":" in pair:
            phone, code = pair.split(":", 1)
            result[phone.strip()] = code.strip()
    return result


def _generate_code(phone: str) -> str:
    fixed = _test_numbers().get(phone)
    if fixed:
        return fixed
    if settings.OTP_PROVIDER == "static":
        return settings.OTP_STATIC_CODE
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


async def _deliver(phone: str, code: str) -> None:
    provider = settings.OTP_PROVIDER
    if phone in _test_numbers() or provider == "static":
        return  # no SMS
    if provider == "console":
        logger.warning(f"[OTP console] OTP for {phone}: {code}")
        return
    if provider == "msg91":
        if not settings.MSG91_AUTH_KEY or not settings.MSG91_TEMPLATE_ID:
            logger.error("OTP_PROVIDER=msg91 but MSG91_AUTH_KEY / MSG91_TEMPLATE_ID are not set")
            raise HTTPException(status_code=503, detail="OTP service is not configured.")
        payload = {
            "template_id": settings.MSG91_TEMPLATE_ID,
            "short_url": "0",
            "recipients": [{"mobiles": f"91{phone}", settings.MSG91_OTP_VAR: code}],
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    MSG91_FLOW_URL,
                    headers={"authkey": settings.MSG91_AUTH_KEY, "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.error(f"MSG91 request failed: {exc}")
            raise HTTPException(status_code=502, detail="Could not send OTP. Please try again.")
        if response.status_code >= 400 or '"type":"error"' in response.text.replace(" ", ""):
            logger.error(f"MSG91 send failed: {response.status_code} {response.text}")
            raise HTTPException(status_code=502, detail="Could not send OTP. Please try again.")
        return
    raise HTTPException(status_code=503, detail="OTP service is not configured.")


async def send_otp(phone: str, purpose: str) -> None:
    """Create a new OTP for phone+purpose (replacing any earlier one) and deliver it."""
    now = datetime.now(timezone.utc)
    previous = await OtpCode.find(OtpCode.phone == phone, OtpCode.purpose == purpose).to_list()
    if previous:
        latest = max(_utc(p.created_at) for p in previous)
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - (now - latest).total_seconds()
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {int(wait) + 1} seconds before requesting another OTP.",
            )

    code = _generate_code(phone)
    await _deliver(phone, code)  # send first: if delivery fails, the old OTP stays usable

    await OtpCode.find(OtpCode.phone == phone, OtpCode.purpose == purpose).delete()
    await OtpCode(
        phone=phone,
        purpose=purpose,
        code_hash=_hash(phone, purpose, code),
        expires_at=now + timedelta(seconds=settings.OTP_EXPIRE_SECONDS),
        created_at=now,
    ).insert()


async def verify_otp(phone: str, purpose: str, code: str) -> None:
    """Raise HTTPException unless code is the current, unexpired OTP. A correct OTP is consumed."""
    otp = await OtpCode.find_one(OtpCode.phone == phone, OtpCode.purpose == purpose)
    if not otp or _utc(otp.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired or not requested. Please request a new OTP.")
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        await otp.delete()
        raise HTTPException(status_code=429, detail="Too many wrong attempts. Please request a new OTP.")
    if not hmac.compare_digest(otp.code_hash, _hash(phone, purpose, (code or "").strip())):
        otp.attempts += 1
        await otp.save()
        left = settings.OTP_MAX_ATTEMPTS - otp.attempts
        if left <= 0:
            await otp.delete()
            raise HTTPException(status_code=429, detail="Too many wrong attempts. Please request a new OTP.")
        raise HTTPException(status_code=400, detail=f"Invalid OTP. {left} attempt(s) left.")
    await otp.delete()  # single use
