"""
OTP login/registration: the OTP is checked on the server, whatever OTP_PROVIDER is.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI


@pytest_asyncio.fixture
async def client(monkeypatch):
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.api.routes.auth import router
    from app.core.config import settings
    from app.middleware.rate_limiter import limiter
    from app.models.otp import OtpCode
    from app.models.user import User

    await init_beanie(database=AsyncMongoMockClient()["otp_test"], document_models=[User, OtpCode])
    monkeypatch.setattr(settings, "OTP_PROVIDER", "static")
    monkeypatch.setattr(settings, "OTP_STATIC_CODE", "1234")
    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(settings, "OTP_TEST_NUMBERS", "")
    monkeypatch.setattr(limiter, "enabled", False)

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _register(client, otp="1234", phone="9876543210"):
    r = await client.post("/auth/send-otp", json={"phone": phone, "purpose": "register", "email": "a@b.com"})
    assert r.status_code == 200, r.text
    return await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": phone, "otp": otp}
    )


@pytest.mark.asyncio
async def test_register_and_login_with_static_otp(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    assert r.json()["token"]

    assert (await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "login"})).status_code == 200
    r = await client.post("/auth/login", json={"phone": "9876543210", "otp": "1234"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_login_without_requesting_otp_is_rejected(client):
    """Previously the backend ignored the OTP; a direct API call must now fail."""
    assert (await _register(client)).status_code == 201
    r = await client.post("/auth/login", json={"phone": "9876543210", "otp": "1234"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_wrong_otp_rejected_and_otp_is_single_use(client):
    assert (await _register(client, otp="0000")).status_code == 400
    r = await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "1234"}
    )
    assert r.status_code == 201  # wrong try does not burn the OTP

    await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "login"})
    assert (await client.post("/auth/login", json={"phone": "9876543210", "otp": "1234"})).status_code == 200
    assert (await client.post("/auth/login", json={"phone": "9876543210", "otp": "1234"})).status_code == 400


@pytest.mark.asyncio
async def test_too_many_wrong_attempts_locks_otp(client):
    from app.core.config import settings

    await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register", "email": "a@b.com"})
    body = {"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "9999"}
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        await client.post("/auth/register", json=body)
    body["otp"] = "1234"
    assert (await client.post("/auth/register", json=body)).status_code in (400, 429)


@pytest.mark.asyncio
async def test_expired_otp_rejected(client):
    from app.models.otp import OtpCode

    await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register", "email": "a@b.com"})
    await OtpCode.get_motor_collection().update_one(
        {"phone": "9876543210"},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    r = await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "1234"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_login_otp_for_unknown_number_is_404(client):
    r = await client.post("/auth/send-otp", json={"phone": "9123456789", "purpose": "login"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_register_otp_for_taken_number_is_400(client):
    assert (await _register(client)).status_code == 201
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_resend_cooldown(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30)
    body = {"phone": "9876543210", "purpose": "register"}
    assert (await client.post("/auth/send-otp", json=body)).status_code == 200
    assert (await client.post("/auth/send-otp", json=body)).status_code == 429


@pytest.mark.asyncio
async def test_console_provider_generates_random_otp_and_sends_no_sms(client, monkeypatch):
    from app.core.config import settings
    from app.services import otp_service

    monkeypatch.setattr(settings, "OTP_PROVIDER", "console")
    sent = []
    monkeypatch.setattr(otp_service, "_generate_code", lambda phone: "482913")
    real_deliver = otp_service._deliver

    async def spy(phone, code):
        sent.append(code)
        await real_deliver(phone, code)

    monkeypatch.setattr(otp_service, "_deliver", spy)
    assert (await _register(client, otp="1234")).status_code == 400
    assert sent == ["482913"]


@pytest.mark.asyncio
async def test_msg91_without_keys_fails_cleanly(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OTP_PROVIDER", "msg91")
    monkeypatch.setattr(settings, "MSG91_AUTH_KEY", None)
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    assert r.status_code == 503
