"""
Check the Twilio settings in .env before switching OTP_PROVIDER to twilio.

    python -m scripts.twilio_check                 # checks the SID/token only, sends nothing
    python -m scripts.twilio_check 9876543210      # also sends one test SMS to that number

On a Twilio trial account the number must first be added under
Phone Numbers > Manage > Verified Caller IDs in the Twilio Console.
"""

import asyncio
import sys

import httpx

from app.core.config import settings
from app.services.otp_service import _send_twilio_sms


async def main() -> int:
    sid, token = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
    sender = settings.TWILIO_MESSAGING_SERVICE_SID or settings.TWILIO_FROM_NUMBER
    missing = [
        name
        for name, value in [
            ("TWILIO_ACCOUNT_SID", sid),
            ("TWILIO_AUTH_TOKEN", token),
            ("TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID", sender),
        ]
        if not value
    ]
    if missing:
        print("Missing in .env:", ", ".join(missing))
        return 1

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json", auth=(sid, token))
    if r.status_code != 200:
        print(f"Twilio rejected the credentials (HTTP {r.status_code}). Check the SID and auth token.")
        return 1
    account = r.json()
    print(f"Credentials OK. Account status: {account.get('status')}, type: {account.get('type')}")
    print(f"Sender: {sender}")
    print(f"OTP_PROVIDER is currently: {settings.OTP_PROVIDER}")

    if len(sys.argv) > 1:
        phone = sys.argv[1]
        try:
            await _send_twilio_sms(phone, "0000")
        except Exception as exc:  # HTTPException carries the user-facing reason
            print(f"Test SMS failed: {getattr(exc, 'detail', exc)} (see the log line above for Twilio's code)")
            return 1
        print(f"Test SMS sent to {settings.OTP_SMS_COUNTRY_CODE}{phone} (code 0000, not a real login OTP).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
