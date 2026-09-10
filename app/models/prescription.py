"""
Prescription document model — migrated from Prescription.js (Mongoose).
"""

from datetime import datetime, timezone
from typing import Optional, Union, Any

from beanie import Document, Indexed, before_event, Insert, Replace, SaveChanges, Update
from pydantic import Field, field_validator

from app.models.counter import Counter


class Prescription(Document):
    """Medical prescription document stored in MongoDB."""

    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    hospital_name: Optional[str] = Field(default="", alias="hospitalName", max_length=255)
    doctor_name: Optional[str] = Field(default="", alias="doctorName", max_length=255)
    patient_name: Optional[str] = Field(default="", alias="patientName", max_length=255)
    prescription_number: Optional[str] = Field(default="", alias="prescriptionNumber", max_length=255)
    visit_date: Optional[Union[datetime, str]] = Field(default="", alias="visitDate")
    diagnosis: Optional[str] = Field(default="", alias="diagnosis", max_length=1000)
    grid_fs_file_id: Optional[str] = Field(default="", alias="gridFsFileId")
    original_file_name: Optional[str] = Field(default="", alias="originalFileName", max_length=255)
    mime_type: Optional[str] = Field(default="", alias="mimeType", max_length=255)
    file_size: Optional[int] = Field(default=0, alias="fileSize", ge=0)
    sequence_number: Optional[int] = Field(default=None, alias="sequenceNumber", ge=0)

    # Extraction Data
    is_manual: bool = Field(default=False, alias="isManual")
    extracted_prescription_text: Optional[str] = Field(default="", alias="extractedPrescriptionText")
    extracted_prescription_json: Optional[dict] = Field(default_factory=dict, alias="extractedPrescriptionJson")
    processing_status: str = Field(default="completed", alias="processingStatus") # pending, processing, completed, failed

    # Agreement subdocument
    agreement: Optional[dict] = None

    # Base schema fields
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    @field_validator(
        "hospital_name",
        "doctor_name",
        "patient_name",
        "prescription_number",
        "diagnosis",
        "grid_fs_file_id",
        "original_file_name",
        "mime_type",
        "extracted_prescription_text",
        mode="before",
    )
    @classmethod
    def sanitize_string_fields(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("visit_date", mode="before")
    @classmethod
    def sanitize_visit_date(cls, v):
        if v is None:
            return ""
        return v

    @field_validator("file_size", mode="before")
    @classmethod
    def sanitize_file_size(cls, v):
        if v is None:
            return 0
        return v

    @field_validator("extracted_prescription_json", mode="before")
    @classmethod
    def sanitize_dict_fields(cls, v):
        if v is None:
            return {}
        return v

    class Settings:
        name = "prescriptions"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event([Insert, Replace, SaveChanges, Update])
    def sanitize_null_strings(self):
        """Ensure no string fields are stored as null in MongoDB."""
        string_fields = [
            "hospital_name", "doctor_name", "patient_name", "prescription_number",
            "diagnosis", "grid_fs_file_id", "original_file_name", "mime_type",
            "extracted_prescription_text"
        ]
        for field in string_fields:
            if getattr(self, field, None) is None:
                setattr(self, field, "")
        if self.visit_date is None:
            self.visit_date = ""
        if self.file_size is None:
            self.file_size = 0
        if self.extracted_prescription_json is None:
            self.extracted_prescription_json = {}

    @before_event(Insert)
    async def assign_sequence_number(self):
        """Auto-assign sequence number on insert."""
        self.sequence_number = await Counter.get_next_sequence(self.user_id, "Prescription")
