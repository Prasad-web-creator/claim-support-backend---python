"""
Email OTP login, registration and verification.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


@pytest_asyncio.fixture
async def client(monkeypatch):
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.api.routes.auth import router
    from app.core.config import settings
    from app.middleware.rate_limiter import limiter
    from app.models.otp import OtpCode
    from app.models.user import User
    from app.services import otp_service

    database = AsyncMongoMockClient()["otp_test"]
    await init_beanie(database=database, document_models=[User, OtpCode])
    # mongomock drops partialFilterExpression in create_indexes (Beanie's bulk initializer).
    await database.users.drop_index("googleSubject_1")
    for index in User.Settings.indexes:
        if "partialFilterExpression" not in index.document:
            continue
        await database.users.create_index(list(index.document["key"].items()), **{
            key: value for key, value in index.document.items() if key != "key"
        })
    monkeypatch.setattr(settings, "EMAIL_OTP_PROVIDER", "disabled")
    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(settings, "OTP_LENGTH", 6)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")

    real_deliver = otp_service._deliver

    async def offline_delivery(target, code):
        if settings.EMAIL_OTP_PROVIDER != "disabled":
            await real_deliver(target, code)

    monkeypatch.setattr(otp_service, "_generate_code", lambda: "482913")
    monkeypatch.setattr(otp_service, "_deliver", offline_delivery)
    monkeypatch.setattr(limiter, "enabled", False)

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _register(client, otp="482913", phone="9876543210"):
    r = await client.post("/auth/send-otp", json={"phone": phone, "purpose": "register", "email": "a@b.com"})
    assert r.status_code == 200, r.text
    return await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": phone, "otp": otp}
    )


@pytest.mark.asyncio
async def test_register_and_login_with_otp(client):
    r = await _register(client)
    assert r.status_code == 201, r.text
    assert r.json()["token"]

    assert (await client.post("/auth/send-otp", json={"email": "a@b.com", "purpose": "login"})).status_code == 200
    r = await client.post("/auth/login", json={"email": "a@b.com", "otp": "482913"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_login_without_requesting_otp_is_rejected(client):
    """Previously the backend ignored the OTP; a direct API call must now fail."""
    assert (await _register(client)).status_code == 201
    r = await client.post("/auth/login", json={"email": "a@b.com", "otp": "482913"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_wrong_otp_rejected_and_otp_is_single_use(client):
    assert (await _register(client, otp="000000")).status_code == 400
    r = await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "482913"}
    )
    assert r.status_code == 201  # wrong try does not burn the OTP

    await client.post("/auth/send-otp", json={"email": "a@b.com", "purpose": "login"})
    assert (await client.post("/auth/login", json={"email": "a@b.com", "otp": "482913"})).status_code == 200
    assert (await client.post("/auth/login", json={"email": "a@b.com", "otp": "482913"})).status_code == 400


@pytest.mark.asyncio
async def test_too_many_wrong_attempts_locks_otp(client):
    from app.core.config import settings

    await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register", "email": "a@b.com"})
    body = {"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "999999"}
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        await client.post("/auth/register", json=body)
    body["otp"] = "482913"
    assert (await client.post("/auth/register", json=body)).status_code in (400, 429)


@pytest.mark.asyncio
async def test_expired_otp_rejected(client):
    from app.models.otp import OtpCode

    await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register", "email": "a@b.com"})
    await OtpCode.get_motor_collection().update_one(
        {"target": "a@b.com"},
        {"$set": {"valid_until": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    r = await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "482913"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_resend_cooldown(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30)
    body = {"email": "a@b.com", "purpose": "register"}
    assert (await client.post("/auth/send-otp", json=body)).status_code == 200
    assert (await client.post("/auth/send-otp", json=body)).status_code == 429


PHONE, EMAIL = "9876543210", "a@b.com"


async def _register_via(client, otp="482913", email=EMAIL, phone=PHONE):
    r = await client.post(
        "/auth/send-otp", json={"channel": "email", "purpose": "register", "email": email}
    )
    assert r.status_code == 200, r.text
    return await client.post(
        "/auth/register",
        json={"name": "A", "email": email, "phone": phone, "otp": otp, "channel": "email"},
    )


async def _login_via(client, otp="482913", email=EMAIL):
    r = await client.post("/auth/send-otp", json={"channel": "email", "purpose": "login", "email": email})
    if r.status_code != 200:
        return r
    return await client.post("/auth/login", json={"channel": "email", "otp": otp, "email": email})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_register_with_email_otp_then_login_with_email(client):
    r = await _register_via(client)
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["emailVerified"] is True

    r = await _login_via(client)
    assert r.status_code == 200, r.text
    # Email is matched case-insensitively.
    assert (await _login_via(client, email="A@B.com")).status_code == 200


@pytest.mark.asyncio
async def test_sms_channel_is_rejected_by_auth_routes(client):
    token = (await _register_via(client)).json()["token"]
    for endpoint, fields in (
        ("/auth/send-otp", {"email": EMAIL, "purpose": "login"}),
        ("/auth/register", {"email": EMAIL, "name": "A", "otp": "482913"}),
        ("/auth/login", {"email": EMAIL, "otp": "482913"}),
        ("/auth/me/verify/send-otp", {}),
        ("/auth/me/verify", {"otp": "482913"}),
    ):
        result = await client.post(endpoint, json={"channel": "sms", **fields}, headers=_auth(token))
        assert result.status_code == 422


@pytest.mark.asyncio
async def test_legacy_account_can_verify_email_from_its_profile(client):
    from app.core.security import create_access_token
    from app.models.user import User

    account = await User(name="Old", email="Old@Example.com", phone="12345").insert()

    headers = _auth(create_access_token(str(account.id)))
    sent = await client.post("/auth/me/verify/send-otp", json={"channel": "email"}, headers=headers)
    assert sent.status_code == 200
    confirmed = await client.post("/auth/me/verify", json={"otp": "482913"}, headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["emailVerified"] is True
    assert confirmed.json()["phone"] == "12345"
    assert (await _login_via(client, email="old@example.com")).status_code == 200
    assert (await client.post("/auth/me/verify/send-otp", json={}, headers=headers)).status_code == 400


@pytest.mark.asyncio
async def test_no_duplicate_email_or_phone_accounts(client):
    assert (await _register_via(client)).status_code == 201
    for email, phone in [("A@b.com", "9123456789"), ("new@b.com", PHONE)]:
        assert (await _register_via(client, email=email, phone=phone)).status_code == 400


@pytest.mark.asyncio
async def test_changing_email_requires_verifying_it_again(client):
    token = (await _register_via(client)).json()["token"]
    headers = _auth(token)
    address = {"newEmail": "new@b.com"}
    r = await client.put("/auth/me", json={"name": "A", "email": "new@b.com"}, headers=headers)
    assert r.status_code == 400

    r = await client.post("/auth/me/change-email/send-otp", json=address, headers=headers)
    assert r.status_code == 200
    r = await client.post("/auth/me/change-email/verify", json={**address, "otp": "482913"}, headers=headers)
    assert r.status_code == 200
    assert (await _login_via(client, email="new@b.com")).status_code == 200


@pytest.mark.asyncio
async def test_missing_or_invalid_contact_for_channel_is_422(client):
    r = await client.post("/auth/send-otp", json={"channel": "email", "purpose": "login", "phone": PHONE})
    assert r.status_code == 422
    r = await client.post("/auth/send-otp", json={"channel": "email", "purpose": "login", "email": "not-an-email"})
    assert r.status_code == 422


# ─── SMTP delivery ────────────────────────────────────────────────────────────


class FakeSMTP:
    """Stands in for an authenticated smtplib connection."""

    def __init__(self, error=None):
        self.error = error
        self.sent = []

    def send_message(self, msg):
        if self.error:
            raise self.error
        self.sent.append(msg)

    def quit(self):
        pass


def _use_smtp(monkeypatch, server=None, connect_error=None):
    from app.core.config import settings
    from app.services import otp_service

    monkeypatch.setattr(settings, "EMAIL_OTP_PROVIDER", "smtp")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app pass word")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_FROM_NAME", "claimsupport")

    def connect():
        if connect_error:
            raise connect_error
        return server

    monkeypatch.setattr(otp_service, "_smtp_connect", connect)


@pytest.mark.asyncio
async def test_smtp_sends_email_and_the_sent_code_registers(client, monkeypatch):
    server = FakeSMTP()
    _use_smtp(monkeypatch, server)
    r = await client.post(
        "/auth/send-otp",
        json={"channel": "email", "purpose": "register", "email": "User@Mail.com", "phone": PHONE},
    )
    assert r.status_code == 200, r.text
    assert r.json()["destination"] == "us**@mail.com"
    assert r.json()["delivered"] is True and r.json()["testOtp"] is None
    assert r.json()["message"] == "OTP sent to us**@mail.com"

    assert len(server.sent) == 1
    msg = server.sent[0]
    assert msg["To"] == "user@mail.com"
    assert "claimsupport" in msg["From"] and "sender@example.com" in msg["From"]
    text = msg.get_body(("plain",)).get_content()
    code = re.search(r"\b\d{6}\b", text).group(0)
    assert len(code) == 6 and code.isdigit()
    assert code not in msg["Subject"]

    r = await client.post(
        "/auth/register",
        json={"name": "A", "email": "User@Mail.com", "phone": PHONE, "otp": code, "channel": "email"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["email"] == "user@mail.com"


@pytest.mark.asyncio
async def test_smtp_failures_give_clear_errors_and_store_no_otp(client, monkeypatch):
    import smtplib

    from app.models.otp import OtpCode

    body = {"channel": "email", "purpose": "register", "email": EMAIL}
    cases = [
        (None, smtplib.SMTPAuthenticationError(535, b"bad creds"), 503),
        (FakeSMTP(smtplib.SMTPRecipientsRefused({EMAIL: (550, b"no such user")})), None, 400),
        (None, OSError("network down"), 502),
        (FakeSMTP(smtplib.SMTPServerDisconnected("gone")), None, 502),
    ]
    for server, connect_error, expected in cases:
        _use_smtp(monkeypatch, server, connect_error)
        r = await client.post("/auth/send-otp", json=body)
        assert r.status_code == expected, (expected, r.text)
    assert await OtpCode.find_all().count() == 0


@pytest.mark.asyncio
async def test_smtp_without_credentials_fails_cleanly(client, monkeypatch):
    from app.core.config import settings

    _use_smtp(monkeypatch, FakeSMTP())
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)
    r = await client.post("/auth/send-otp", json={"channel": "email", "purpose": "register", "email": EMAIL})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_unverified_email_logs_in_with_email_otp_and_becomes_verified(client):
    """Email OTP can verify an existing account during login."""
    from app.models.user import User

    await _register_via(client)
    user = await User.find_one(User.email == EMAIL)
    user.email_verified = False
    await user.save()

    r = await client.post("/auth/send-otp", json={"channel": "email", "purpose": "login", "email": EMAIL})
    assert r.status_code == 200, r.text
    r = await _login_via(client)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["emailVerified"] is True


async def test_email_registration_without_phone_allows_many_users(client):
    """The app no longer asks for a mobile number; each user registers with email only."""
    for email in ("one@b.com", "two@b.com"):
        r = await client.post("/auth/send-otp", json={"channel": "email", "purpose": "register", "email": email})
        assert r.status_code == 200, r.text
        r = await client.post(
            "/auth/register", json={"name": "A", "email": email, "otp": "482913", "channel": "email"}
        )
        assert r.status_code == 201, r.text
        assert r.json()["user"]["phone"] is None


async def test_email_otp_responses_hide_account_presence_until_inbox_proof(client):
    assert (await _register_via(client)).status_code == 201
    contacts = [{"channel": "email", "email": email} for email in (EMAIL, "n@b.com")]
    for purpose in ("login", "register"):
        receipts = [await client.post("/auth/send-otp", json={**body, "purpose": purpose}) for body in contacts]
        assert [receipt.status_code for receipt in receipts] == [200, 200]
        first, second = [receipt.json() for receipt in receipts]
        for key in ("destination", "message"):
            first.pop(key)
            second.pop(key)
        assert first == second and first["testOtp"] is None

    failures = [await client.post("/auth/login", json={**body, "otp": "000000"}) for body in contacts]
    assert [result.status_code for result in failures] == [400, 400]
    assert failures[0].json() == failures[1].json()


async def test_otp_reservation_and_verification_are_atomic(client, monkeypatch):
    from app.core.config import settings
    from app.services import otp_service

    assert (await _register_via(client)).status_code == 201
    started, release = asyncio.Event(), asyncio.Event()

    async def hold_delivery(target, code):
        started.set()
        await release.wait()

    monkeypatch.setattr(otp_service, "_deliver", hold_delivery)
    monkeypatch.setattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 60)
    body = {"channel": "email", "email": EMAIL}
    send = {**body, "purpose": "login"}
    first = asyncio.create_task(client.post("/auth/send-otp", json=send))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        assert (await client.post("/auth/send-otp", json=send)).status_code == 429
    finally:
        release.set()
        assert (await first).status_code == 200

    logins = await asyncio.gather(
        *(client.post("/auth/login", json={**body, "otp": "482913"}) for _ in range(2))
    )
    assert sorted(response.status_code for response in logins) == [200, 400]
    assert (await client.post("/auth/send-otp", json=send)).status_code == 429


async def test_email_send_is_rate_limited_across_recipients_for_the_same_ip(client, monkeypatch):
    from app.core.config import settings
    from app.middleware.rate_limiter import limiter

    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(settings, "OTP_SEND_RATE_LIMIT", "1/minute")
    limiter.reset()
    try:
        body = {"channel": "email", "purpose": "login"}
        assert (await client.post("/auth/send-otp", json={**body, "email": "first@example.com"})).status_code == 200
        assert (await client.post("/auth/send-otp", json={**body, "email": "second@example.com"})).status_code == 429
    finally:
        limiter.reset()


async def test_change_email_proof_is_bound_to_requesting_account(client):
    first = await _register_via(client, email="first@example.com", phone=None)
    second = await _register_via(client, email="second@example.com", phone=None)
    first_headers = _auth(first.json()["token"])
    second_headers = _auth(second.json()["token"])
    address = {"newEmail": "next@example.com"}
    assert (await client.post("/auth/me/change-email/send-otp", json=address, headers=first_headers)).is_success
    body = {**address, "otp": "482913"}
    assert (await client.post("/auth/me/change-email/verify", json=body, headers=second_headers)).status_code == 400
    assert (await client.post("/auth/me/change-email/verify", json=body, headers=first_headers)).is_success


async def test_existing_sessions_can_refresh_and_token_rotation_is_atomic(client):
    from app.core.security import hash_refresh_token
    from app.models.user import User

    signed_in = await _register_via(client)
    original = signed_in.json()["refreshToken"]
    user = await User.find_one(User.email == EMAIL)
    assert user.refresh_token == hash_refresh_token(original)
    rejected = await client.post("/auth/refresh", json={"refreshToken": user.refresh_token})
    assert rejected.status_code == 401

    responses = await asyncio.gather(
        *(client.post("/auth/refresh", json={"refreshToken": original}) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == [200, 401]
    refreshed = next(response.json() for response in responses if response.is_success)
    assert refreshed["refreshToken"] != original
    assert refreshed["user"]["id"] == signed_in.json()["user"]["id"]


async def test_legacy_refresh_token_is_migrated_once(client):
    from app.core.security import hash_refresh_token
    from app.models.user import User

    signed_in = await _register_via(client)
    original = signed_in.json()["refreshToken"]
    user = await User.find_one(User.email == EMAIL)
    await User.get_motor_collection().update_one({"_id": user.id}, {"$set": {"refreshToken": original}})

    response = await client.post("/auth/refresh", json={"refreshToken": original})
    assert response.status_code == 200
    assert (await User.get(user.id)).refresh_token == hash_refresh_token(response.json()["refreshToken"])
    assert (await client.post("/auth/refresh", json={"refreshToken": original})).status_code == 401


async def test_gmail_email_otp_registers_through_auth_api(client, monkeypatch):
    from app.core.config import settings
    from app.services import otp_service
    from app.services.gmail_delivery import GMAIL_SEND_URL, GOOGLE_TOKEN_URL, GmailDelivery

    requests = []

    def send(request):
        requests.append(request)
        if str(request.url) == GOOGLE_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "test-access", "expires_in": 3600})
        assert str(request.url) == GMAIL_SEND_URL
        assert request.headers["Authorization"] == "Bearer test-access"
        return httpx.Response(200, json={"id": "test-message"})

    monkeypatch.setattr(settings, "EMAIL_OTP_PROVIDER", "gmail")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_ID", "test-app")
    monkeypatch.setattr(settings, "GMAIL_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(settings, "GMAIL_REFRESH_TOKEN", "test-refresh")
    monkeypatch.setattr(settings, "GMAIL_FROM_ADDRESS", "sender@example.com")
    monkeypatch.setattr(otp_service, "_gmail_delivery", GmailDelivery())

    def mock_client(*, trust_env):
        assert trust_env is False
        return httpx.AsyncClient(transport=httpx.MockTransport(send))

    monkeypatch.setattr(otp_service, "_http_client", mock_client)
    response = await _register_via(client)
    assert response.status_code == 201
    assert response.json()["user"]["emailVerified"] is True
    assert len(requests) == 2


async def test_resend_delivers_only_a_hashed_single_use_code(client, monkeypatch):
    import json

    from app.core.config import settings
    from app.models.otp import OtpCode
    from app.services import otp_service

    captured = []

    def send(request):
        captured.append(request)
        return httpx.Response(200, json={"id": "test"})

    monkeypatch.setattr(settings, "EMAIL_OTP_PROVIDER", "resend")
    monkeypatch.setattr(settings, "EMAIL_API_KEY", "provider-key-stub")
    monkeypatch.setattr(settings, "EMAIL_FROM_ADDRESS", "verify@example.com")
    monkeypatch.setattr(
        otp_service, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(send))
    )
    body = {"channel": "email", "email": EMAIL}
    response = await client.post("/auth/send-otp", json={**body, "purpose": "register"})
    assert response.status_code == 200 and "482913" not in response.text
    request, = captured
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == "Bearer provider-key-stub"
    payload = json.loads(request.content)
    assert payload["to"] == [EMAIL]
    assert "482913" in payload["text"] and "482913" not in payload["subject"]
    otp = await OtpCode.find_one(OtpCode.target == EMAIL)
    assert otp.code_hash and len(otp.code_hash) == 64 and otp.code_hash != "482913"
    assert (await client.post("/auth/register", json={**body, "name": "A", "otp": "482913"})).status_code == 201
    otp = await OtpCode.get(otp.id)
    assert otp.state == "used" and otp.code_hash is None


async def test_google_matches_verified_email_and_signs_in_by_subject(client, monkeypatch):
    from app.api.routes import auth
    from app.models.user import User
    from app.services.google_sign_in import GoogleIdentity

    existing = await _register_via(client, email="owner@gmail.com", phone=None)
    existing_id = existing.json()["user"]["id"]
    identity = GoogleIdentity("test-subject", "owner@gmail.com", "Google User", True)

    async def verify(token):
        return identity

    monkeypatch.setattr(auth, "verify_google_token", verify)
    signed_in = await client.post("/auth/google", json={"idToken": "mock-token"})
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["id"] == existing_id
    assert signed_in.json()["user"]["googleLinked"] is True

    identity = GoogleIdentity("test-subject", "different@gmail.com", "Google User", True)
    signed_in = await client.post("/auth/google", json={"idToken": "mock-token"})
    assert signed_in.json()["user"]["id"] == existing_id
    assert await User.find_all().count() == 1


async def test_google_nonhosted_email_needs_explicit_authenticated_link(client, monkeypatch):
    from app.api.routes import auth
    from app.models.user import User
    from app.services.google_sign_in import GoogleIdentity

    async def verify(token):
        return GoogleIdentity("test-subject", "owner@example.com", "Google User", False)

    monkeypatch.setattr(auth, "verify_google_token", verify)
    payload = {"idToken": "mock-token"}
    assert (await client.post("/auth/google", json=payload)).status_code == 409
    assert await User.find_all().count() == 0
    existing = await _register_via(client, email="owner@example.com", phone=None)
    assert (await client.post("/auth/google", json=payload)).status_code == 409
    assert (await client.post("/auth/me/google", json=payload)).status_code in (401, 403)
    linked = await client.post("/auth/me/google", json=payload, headers=_auth(existing.json()["token"]))
    assert linked.status_code == 200 and linked.json()["googleLinked"]
    signed_in = await client.post("/auth/google", json=payload)
    assert signed_in.json()["user"]["id"] == existing.json()["user"]["id"]
    assert await User.find_all().count() == 1


async def test_google_requires_valid_signature_audience_issuer_expiry_and_email(client, monkeypatch):
    import json
    from types import SimpleNamespace

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt, jwt

    from app.core.config import settings
    from app.services import google_sign_in

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def sign(private_key, claims):
        signer = crypt.RSASigner.from_string(
            private_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
            key_id="local-key",
        )
        return jwt.encode(signer, claims).decode()

    def certs(*args, **kwargs):
        assert kwargs["timeout"] == settings.AUTH_REQUEST_TIMEOUT_SECONDS
        return SimpleNamespace(status=200, data=json.dumps({"local-key": public_key.decode()}).encode())

    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(google_sign_in, "_fetch_certs", certs)
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {
        "aud": settings.GOOGLE_CLIENT_ID, "iss": "https://accounts.google.com",
        "exp": now + 300, "iat": now - 10, "sub": "test-subject",
        "email": "owner@gmail.com", "email_verified": True,
    }
    for invalid in (
        sign(key, {**claims, "aud": "some-other-client"}),
        sign(key, {**claims, "iss": "https://some-other-issuer.example"}),
        sign(key, {**claims, "exp": now - 5}),
        sign(key, {**claims, "email_verified": False}),
        sign(rsa.generate_private_key(public_exponent=65537, key_size=2048), claims),
    ):
        assert (await client.post("/auth/google", json={"idToken": invalid})).status_code == 401

    signed_in = await client.post("/auth/google", json={"idToken": sign(key, claims)})
    assert signed_in.status_code == 200 and signed_in.json()["user"]["email"] == "owner@gmail.com"
