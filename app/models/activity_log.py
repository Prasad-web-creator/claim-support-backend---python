"""
ActivityLog document model — migrated from ActivityLog.js (Mongoose).
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed
from pydantic import Field


class ActivityLog(Document):
    """User activity log entry stored in MongoDB."""

    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    action: str = Field(max_length=255)  # e.g. 'Created Policy'
    entity_type: str = Field(alias="entityType", max_length=100)  # e.g. 'Policy'
    entity_id: Optional[str] = Field(default=None, alias="entityId")

    # Base schema fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    created_by: Optional[str] = Field(default=None, alias="createdBy")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")
    is_deleted: bool = Field(default=False, alias="isDeleted")

    class Settings:
        name = "activitylogs"
        use_state_management = True

    class Config:
        populate_by_name = True
