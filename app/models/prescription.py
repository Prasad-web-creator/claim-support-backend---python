"""
Prescription document model — migrated from Prescription.js (Mongoose).
"""

from datetime import datetime
from typing import Optional

from beanie import Document, Indexed, before_event, Insert
from pydantic import Field

from app.models.counter import Counter


class Prescription(Document):
    """Medical prescription document stored in MongoDB."""

    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    hospital_name: Optional[str] = Field(default=None, alias="hospitalName", max_length=255)
    doctor_name: Optional[str] = Field(default=None, alias="doctorName", max_length=255)
    visit_date: Optional[datetime] = Field(default=None, alias="visitDate")
    diagnosis: Optional[str] = Field(default=None, alias="diagnosis", max_length=1000)
    grid_fs_file_id: Optional[str] = Field(default=None, alias="gridFsFileId")
    original_file_name: Optional[str] = Field(default=None, alias="originalFileName", max_length=255)
    mime_type: Optional[str] = Field(default=None, alias="mimeType", max_length=255)
    file_size: Optional[int] = Field(default=None, alias="fileSize", ge=0)
    sequence_number: Optional[int] = Field(default=None, alias="sequenceNumber", ge=0)

    # Agreement subdocument
    agreement: Optional[dict] = None

    # Base schema fields
    created_by: Optional[str] = Field(default=None, alias="createdBy")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")
    is_deleted: bool = Field(default=False, alias="isDeleted")

    class Settings:
        name = "prescriptions"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event(Insert)
    async def assign_sequence_number(self):
        """Auto-assign sequence number on insert."""
        self.sequence_number = await Counter.get_next_sequence(self.user_id, "Prescription")
