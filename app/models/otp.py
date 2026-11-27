"""
One-time password document. Codes are stored hashed and removed by MongoDB after expiry.
"""

from datetime import datetime, timezone

import pymongo
from beanie import Document
from pydantic import Field


class OtpCode(Document):
    """A pending email OTP for a given destination and purpose."""

    channel: str = "email"
    target: str
    purpose: str
    code_hash: str | None = None
    attempts: int = 0
    generation: str | None = None
    state: str | None = None
    valid_until: datetime | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "otp_codes"
        indexes = [
            [("channel", pymongo.ASCENDING), ("target", pymongo.ASCENDING), ("purpose", pymongo.ASCENDING)],
            # TTL index: MongoDB deletes the document once expires_at has passed.
            pymongo.IndexModel([("expires_at", pymongo.ASCENDING)], expireAfterSeconds=0),
        ]
