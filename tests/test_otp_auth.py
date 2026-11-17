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
async def test_static_mode_says_otp_not_sent_and_gives_test_code(client, monkeypatch):
    """With no SMS/email service set up, the app must not claim the OTP was sent."""
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    body = r.json()
    assert body["delivered"] is False and body["testOtp"] == "1234"
    assert body["message"] == "OTP could not be sent to ******3210. Please use test OTP 1234."

    # Reviewer test numbers never reveal their code.
    from app.core.config import settings

    monkeypatch.setattr(settings, "OTP_TEST_NUMBERS", "9123456789:4321")
    body = (await client.post("/auth/send-otp", json={"phone": "9123456789", "purpose": "register"})).json()
    assert body["delivered"] is False and body["testOtp"] is None and "4321" not in body["message"]


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
        {"target": "9876543210"},
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
    monkeypatch.setattr(otp_service, "_generate_code", lambda channel, target: "482913")
    real_deliver = otp_service._deliver

    async def spy(channel, target, code):
        sent.append(code)
        await real_deliver(channel, target, code)

    monkeypatch.setattr(otp_service, "_deliver", spy)
    assert (await _register(client, otp="1234")).status_code == 400
    assert sent == ["482913"]


def _use_twilio(monkeypatch, handler):
    """Switch to OTP_PROVIDER=twilio with fake credentials; Twilio calls go to `handler`."""
    from app.core.config import settings
    from app.services import otp_service

    monkeypatch.setattr(settings, "OTP_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC_test_sid")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setattr(settings, "TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setattr(settings, "TWILIO_MESSAGING_SERVICE_SID", None)
    monkeypatch.setattr(
        otp_service, "_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_twilio_without_keys_fails_cleanly(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OTP_PROVIDER", "twilio")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", None)
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_twilio_sends_sms_and_the_sent_code_logs_in(client, monkeypatch):
    from urllib.parse import parse_qs

    requests = []

    def twilio(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"sid": "SM123", "status": "queued"})

    _use_twilio(monkeypatch, twilio)
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    assert r.status_code == 200, r.text

    assert len(requests) == 1
    req = requests[0]
    assert str(req.url) == "https://api.twilio.com/2010-04-01/Accounts/AC_test_sid/Messages.json"
    assert req.headers["Authorization"].startswith("Basic ")
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert form["To"] == "+919876543210"
    assert form["From"] == "+15005550006"
    code = form["Body"].split("OTP is ")[1].split(".")[0]
    assert len(code) == 4 and code.isdigit()
    assert "valid for 5 minutes" in form["Body"]

    # The code from the SMS is the one the server accepts.
    r = await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": code}
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_twilio_messaging_service_used_when_set(client, monkeypatch):
    from urllib.parse import parse_qs

    from app.core.config import settings

    forms = []

    def twilio(request):
        forms.append(parse_qs(request.content.decode()))
        return httpx.Response(201, json={"sid": "SM123"})

    _use_twilio(monkeypatch, twilio)
    monkeypatch.setattr(settings, "TWILIO_MESSAGING_SERVICE_SID", "MG_test")
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    assert r.status_code == 200, r.text
    assert forms[0]["MessagingServiceSid"] == ["MG_test"]
    assert "From" not in forms[0]


@pytest.mark.asyncio
async def test_twilio_unverified_trial_number_gives_clear_error(client, monkeypatch):
    def twilio(request):
        return httpx.Response(400, json={"code": 21608, "message": "unverified number", "status": 400})

    _use_twilio(monkeypatch, twilio)
    r = await client.post("/auth/send-otp", json={"phone": "9876543210", "purpose": "register"})
    assert r.status_code == 400
    assert "testing" in r.json()["detail"]
    # Nothing was stored, so no OTP can be used.
    r = await client.post(
        "/auth/register", json={"name": "A", "email": "a@b.com", "phone": "9876543210", "otp": "1234"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_twilio_bad_credentials_and_network_errors(client, monkeypatch):
    def bad_auth(request):
        return httpx.Response(401, json={"code": 20003, "message": "Authenticate", "status": 401})

    _use_twilio(monkeypatch, bad_auth)
    body = {"phone": "9876543210", "purpose": "register"}
    assert (await client.post("/auth/send-otp", json=body)).status_code == 503

    def offline(request):
        raise httpx.ConnectError("no route")

    _use_twilio(monkeypatch, offline)
    assert (await client.post("/auth/send-otp", json=body)).status_code == 502


@pytest.mark.asyncio
async def test_test_numbers_never_hit_twilio(client, monkeypatch):
    from app.core.config import settings

    def twilio(request):
        raise AssertionError("Twilio must not be called for a test number")

    _use_twilio(monkeypatch, twilio)
    monkeypatch.setattr(settings, "OTP_TEST_NUMBERS", "9876543210:4321")
    assert (await _register(client, otp="4321")).status_code == 201


# ─── Email OTP and cross-channel login ────────────────────────────────────────

PHONE, EMAIL = "9876543210", "a@b.com"


async def _register_via(client, channel, otp="1234", email=EMAIL, phone=PHONE):
    r = await client.post(
        "/auth/send-otp", json={"channel": channel, "purpose": "register", "phone": phone, "email": email}
    )
    assert r.status_code == 200, r.text
    return await client.post(
        "/auth/register",
        json={"name": "A", "email": email, "phone": phone, "otp": otp, "channel": channel},
    )


async def _login_via(client, channel, otp="1234", **contact):
    contact = contact or ({"phone": PHONE} if channel == "sms" else {"email": EMAIL})
    r = await client.post("/auth/send-otp", json={"channel": channel, "purpose": "login", **contact})
    if r.status_code != 200:
        return r
    return await client.post("/auth/login", json={"channel": channel, "otp": otp, **contact})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def email_static(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMAIL_OTP_PROVIDER", "static")
    monkeypatch.setattr(settings, "OTP_TEST_EMAILS", "")


@pytest.mark.asyncio
async def test_register_with_email_otp_then_login_with_email(client, email_static):
    r = await _register_via(client, "email")
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["emailVerified"] is True and user["phoneVerified"] is False

    r = await _login_via(client, "email")
    assert r.status_code == 200, r.text
    # Email is matched case-insensitively.
    assert (await _login_via(client, "email", email="A@B.com")).status_code == 200


@pytest.mark.asyncio
async def test_unverified_contact_cannot_log_in_until_verified(client, email_static):
    """Registered by email: SMS login is refused until the phone is verified from the profile."""
    token = (await _register_via(client, "email")).json()["token"]

    r = await client.post("/auth/send-otp", json={"channel": "sms", "purpose": "login", "phone": PHONE})
    assert r.status_code == 403
    assert "not verified" in r.json()["detail"]
    # A direct /login call is refused as well.
    r = await client.post("/auth/login", json={"channel": "sms", "phone": PHONE, "otp": "1234"})
    assert r.status_code == 403

    r = await client.post("/auth/me/verify/send-otp", json={"channel": "sms"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    r = await client.post("/auth/me/verify", json={"channel": "sms", "otp": "0000"}, headers=_auth(token))
    assert r.status_code == 400
    r = await client.post("/auth/me/verify", json={"channel": "sms", "otp": "1234"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["phoneVerified"] is True

    # Both channels now log in to the same account.
    a = await _login_via(client, "sms")
    b = await _login_via(client, "email")
    assert a.status_code == b.status_code == 200
    assert a.json()["user"]["id"] == b.json()["user"]["id"]


@pytest.mark.asyncio
async def test_register_with_sms_then_verify_email_and_log_in_by_email(client, email_static):
    token = (await _register_via(client, "sms")).json()["token"]
    assert (await _login_via(client, "email")).status_code == 403

    r = await client.post("/auth/me/verify/send-otp", json={"channel": "email"}, headers=_auth(token))
    assert r.status_code == 200
    r = await client.post("/auth/me/verify", json={"channel": "email", "otp": "1234"}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["emailVerified"] is True
    assert (await _login_via(client, "email")).status_code == 200

    # Verifying again is rejected.
    r = await client.post("/auth/me/verify/send-otp", json={"channel": "email"}, headers=_auth(token))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_no_duplicate_accounts_across_channels(client, email_static):
    assert (await _register_via(client, "email")).status_code == 201
    # Same email (any case) or same phone is refused, whichever channel is used.
    for channel, email, phone in [("sms", "A@b.com", "9123456789"), ("email", "new@b.com", PHONE)]:
        r = await client.post(
            "/auth/send-otp", json={"channel": channel, "purpose": "register", "phone": phone, "email": email}
        )
        assert r.status_code == 400, (channel, r.text)
        r = await client.post(
            "/auth/register",
            json={"name": "B", "email": email, "phone": phone, "otp": "1234", "channel": channel},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_email_and_sms_otps_are_independent(client, monkeypatch):
    """An OTP sent by SMS is not accepted as an email OTP, and vice versa."""
    from app.core.config import settings
    from app.services import otp_service

    monkeypatch.setattr(settings, "OTP_PROVIDER", "console")
    monkeypatch.setattr(settings, "EMAIL_OTP_PROVIDER", "console")
    codes = {"sms": "111111", "email": "222222"}
    monkeypatch.setattr(otp_service, "_generate_code", lambda channel, target: codes[channel])

    for channel in ("sms", "email"):
        r = await client.post(
            "/auth/send-otp", json={"channel": channel, "purpose": "register", "phone": PHONE, "email": EMAIL}
        )
        assert r.status_code == 200
        assert r.json()["otpLength"] == 6

    body = {"name": "A", "email": EMAIL, "phone": PHONE, "channel": "email", "otp": codes["sms"]}
    assert (await client.post("/auth/register", json=body)).status_code == 400
    body["otp"] = codes["email"]
    assert (await client.post("/auth/register", json=body)).status_code == 201


@pytest.mark.asyncio
async def test_legacy_phone_account_can_use_sms_but_must_verify_email(client, email_static):
    from app.models.user import User

    await User(name="Old", email="Old@Example.com", phone="12345").insert()  # no verified flags
    r = await _login_via(client, "sms", phone="12345")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["phoneVerified"] is True
    assert (await _login_via(client, "email", email="old@example.com")).status_code == 403


@pytest.mark.asyncio
async def test_changing_email_requires_verifying_it_again(client, email_static):
    token = (await _register_via(client, "email")).json()["token"]
    r = await client.put("/auth/me", json={"name": "A", "email": "new@b.com"}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["emailVerified"] is False
    assert (await _login_via(client, "email", email="new@b.com")).status_code == 403

    r = await client.post("/auth/me/verify/send-otp", json={"channel": "email"}, headers=_auth(token))
    assert r.status_code == 200
    r = await client.post("/auth/me/verify", json={"channel": "email", "otp": "1234"}, headers=_auth(token))
    assert r.status_code == 200
    assert (await _login_via(client, "email", email="new@b.com")).status_code == 200


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
    monkeypatch.setattr(settings, "OTP_TEST_EMAILS", "")
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
    code = msg["Subject"].rsplit(": ", 1)[1]
    assert len(code) == 4 and code.isdigit()
    assert code in msg.get_body(("plain",)).get_content()

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
async def test_test_emails_never_hit_smtp(client, monkeypatch):
    from app.core.config import settings

    _use_smtp(monkeypatch, connect_error=AssertionError("SMTP must not be called for a test email"))
    monkeypatch.setattr(settings, "OTP_TEST_EMAILS", "Review@Example.com:4321")
    r = await _register_via(client, "email", otp="4321", email="review@example.com")
    assert r.status_code == 201, r.text
