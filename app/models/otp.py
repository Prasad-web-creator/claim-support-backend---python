"""
One-time password document. Codes are stored hashed and removed by MongoDB after expiry.
"""

from datetime import datetime, timezone

import pymongo
from beanie import Document
from pydantic import Field


class OtpCode(Document):
    """A pending OTP for one destination (phone number or email), channel and purpose."""

    channel: str  # sms | email
    target: str  # 10-digit phone number, or lower-cased email address
    purpose: str  # login | register | verify
    code_hash: str
    attempts: int = 0
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "otp_codes"
        indexes = [
            [("channel", pymongo.ASCENDING), ("target", pymongo.ASCENDING), ("purpose", pymongo.ASCENDING)],
            # TTL index: MongoDB deletes the document once expires_at has passed.
            pymongo.IndexModel([("expires_at", pymongo.ASCENDING)], expireAfterSeconds=0),
        ]
