"""
One-time password document. Codes are stored hashed and removed by MongoDB after expiry.
"""

from datetime import datetime, timezone

import pymongo
from beanie import Document
from pydantic import Field


class OtpCode(Document):
    """A pending OTP for one phone number and purpose (login | register)."""

    phone: str
    purpose: str
    code_hash: str
    attempts: int = 0
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "otp_codes"
        indexes = [
            [("phone", pymongo.ASCENDING), ("purpose", pymongo.ASCENDING)],
            # TTL index: MongoDB deletes the document once expires_at has passed.
            pymongo.IndexModel([("expires_at", pymongo.ASCENDING)], expireAfterSeconds=0),
        ]
