import base64
import json
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException

from app.services.gmail_delivery import GMAIL_SEND_URL, GOOGLE_TOKEN_URL, GmailCredentials, GmailDelivery
from app.services.otp_service import _email_message


@pytest.mark.asyncio
async def test_gmail_uses_refresh_grant_and_sends_base64url_mime():
    credentials = GmailCredentials("test-client", "test-secret", "test-refresh-token")
    received = []
    refreshes = 0

    def respond(request):
        nonlocal refreshes
        if str(request.url) == GOOGLE_TOKEN_URL:
            refreshes += 1
            assert parse_qs(request.content.decode()) == {
                "client_id": ["test-client"],
                "client_secret": ["test-secret"],
                "refresh_token": ["test-refresh-token"],
                "grant_type": ["refresh_token"],
            }
            return httpx.Response(200, json={"access_token": "test-access", "expires_in": 3600})

        assert str(request.url) == GMAIL_SEND_URL
        assert request.headers["Authorization"] == "Bearer test-access"
        raw = base64.urlsafe_b64decode(json.loads(request.content)["raw"])
        received.append(BytesParser(policy=policy.default).parsebytes(raw))
        return httpx.Response(200, json={"id": "test-message"})

    delivery = GmailDelivery()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await delivery.send(client, credentials, _email_message("first@example.com", "018223", "otp@example.com"))
        await delivery.send(client, credentials, _email_message("second@example.com", "948551", "otp@example.com"))

    assert refreshes == 1
    assert [item["To"].addresses[0].addr_spec for item in received] == ["first@example.com", "second@example.com"]
    assert received[0]["From"].addresses[0].addr_spec == "otp@example.com"
    assert "018223" in received[0].get_body(preferencelist=("plain",)).get_content()


@pytest.mark.asyncio
async def test_gmail_refreshes_only_rejected_authorization_and_hides_error_payload():
    credentials = GmailCredentials("test-client", "test-secret", "test-refresh-token")
    refreshes = 0
    deliveries = 0

    def respond(request):
        nonlocal refreshes, deliveries
        if str(request.url) == GOOGLE_TOKEN_URL:
            refreshes += 1
            return httpx.Response(200, json={"access_token": f"test-access-{refreshes}", "expires_in": 3600})
        deliveries += 1
        assert request.headers["Authorization"] == f"Bearer test-access-{refreshes}"
        code = 401 if deliveries == 1 else 429
        return httpx.Response(code, json={"error": "private-provider-message"})

    delivery = GmailDelivery()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(HTTPException) as error:
            await delivery.send(client, credentials, _email_message("person@example.com", "018223", "otp@example.com"))

    assert refreshes == deliveries == 2
    assert error.value.status_code == 503
    assert error.value.detail == "Email OTP service is temporarily unavailable."
