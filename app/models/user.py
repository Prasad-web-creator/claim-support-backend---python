"""
User document model — migrated from User.js (Mongoose).
"""

from datetime import datetime
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field


class User(Document):
    """User account document stored in MongoDB."""

    name: str
    email: Indexed(str, unique=True)  # type: ignore[valid-type]
    phone: Optional[Indexed(str)] = None  # type: ignore[valid-type]
    refresh_token: Optional[str] = Field(default=None, alias="refreshToken")
    refresh_token_expiry: Optional[datetime] = Field(default=None, alias="refreshTokenExpiry")

    # Base schema fields
    created_by: Optional[str] = Field(default=None, alias="createdBy")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")
    is_deleted: bool = Field(default=False, alias="isDeleted")

    class Settings:
        name = "users"
        use_state_management = True

    class Config:
        populate_by_name = True
