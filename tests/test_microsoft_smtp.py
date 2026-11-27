import smtplib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException

from app.services import otp_service
from app.services.microsoft_smtp import MicrosoftSmtpTokens


@pytest.mark.asyncio
async def test_client_credentials_request_and_token_reuse():
    calls = []

    def token_response(request):
        calls.append(request)
        assert request.url.host == "login.microsoftonline.com"
        assert request.url.path == "/synthetic-tenant/oauth2/v2.0/token"
        assert parse_qs(request.content.decode()) == {
            "grant_type": ["client_credentials"],
            "client_id": ["synthetic-client"],
            "client_secret": ["synthetic-secret~value"],
            "scope": ["https://outlook.office365.com/.default"],
        }
        return httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 3600})

    config = SimpleNamespace(
        SMTP_TENANT_ID="synthetic-tenant", SMTP_CLIENT_ID="synthetic-client",
        SMTP_CLIENT_SECRET="synthetic-secret~value",
    )
    tokens = MicrosoftSmtpTokens()
    async with httpx.AsyncClient(transport=httpx.MockTransport(token_response)) as client:
        assert await tokens.get(config, client) == "synthetic-token"
        assert await tokens.get(config, client) == "synthetic-token"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_smtp_oauth_delivers_via_tls_before_authentication(monkeypatch):
    smtp = Mock()
    monkeypatch.setattr(otp_service.smtplib, "SMTP", Mock(return_value=smtp))
    for key, value in {
        "EMAIL_OTP_PROVIDER": "smtp_oauth", "SMTP_HOST": "smtp.office365.com", "SMTP_PORT": 587,
        "SMTP_USERNAME": "sender@example.com", "SMTP_FROM_EMAIL": "sender@example.com",
    }.items():
        monkeypatch.setattr(otp_service.settings, key, value)
    monkeypatch.setattr(otp_service, "_smtp_access_token", AsyncMock(return_value="synthetic-token"))

    await otp_service._deliver("recipient@example.com", "123456")
    mechanism, sasl = smtp.auth.call_args.args
    assert mechanism == "XOAUTH2"
    assert sasl() == "user=sender@example.com\x01auth=Bearer synthetic-token\x01\x01"
    assert [call[0] for call in smtp.method_calls] == [
        "starttls", "ehlo_or_helo_if_needed", "auth", "send_message", "quit",
    ]
    assert smtp.send_message.call_args.args[0]["To"] == "recipient@example.com"
    smtp.login.assert_not_called()

    smtp.reset_mock()
    smtp.auth.side_effect = smtplib.SMTPAuthenticationError(535, b"provider rejected")
    with pytest.raises(HTTPException) as failure:
        await otp_service._deliver("recipient@example.com", "123456")
    assert failure.value.status_code == 503
    smtp.close.assert_called_once()
    smtp.send_message.assert_not_called()
