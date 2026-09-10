"""
Policy document model — migrated from Policy.js (Mongoose).
"""

from datetime import datetime, timezone
from typing import Optional, Union, Any

from beanie import Document, Indexed, before_event, Insert, Replace, SaveChanges, Update
from pydantic import Field, field_validator

from app.models.counter import Counter


class Policy(Document):
    """Insurance policy document stored in MongoDB."""

    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    policy_number: Optional[str] = Field(default="", alias="policyNumber", max_length=255)
    policy_name: Optional[str] = Field(default="", alias="policyName", max_length=255)
    policy_holder_name: Optional[str] = Field(default="", alias="policyHolderName", max_length=255)
    insurance_company: Optional[str] = Field(default="", alias="insuranceCompany", max_length=255)
    policy_type: Optional[str] = Field(default="", alias="policyType", max_length=255)
    policy_start_date: Optional[Union[datetime, str]] = Field(default=None, alias="policyStartDate")
    policy_end_date: Optional[Union[datetime, str]] = Field(default=None, alias="policyEndDate")
    coverage_amount: Optional[float] = Field(default=0.0, alias="coverageAmount", ge=0)
    status: str = Field(default="Active", max_length=50)
    grid_fs_file_id: Optional[str] = Field(default="", alias="gridFsFileId")
    original_file_name: Optional[str] = Field(default="", alias="originalFileName", max_length=255)
    mime_type: Optional[str] = Field(default="", alias="mimeType", max_length=255)
    file_size: Optional[int] = Field(default=0, alias="fileSize", ge=0)
    sequence_number: Optional[int] = Field(default=None, alias="sequenceNumber", ge=0)

    # Extraction Data
    extracted_policy_text: Optional[str] = Field(default="", alias="extractedPolicyText")
    extracted_policy_json: Optional[dict] = Field(default_factory=dict, alias="extractedPolicyJson")
    processing_status: str = Field(default="completed", alias="processingStatus") # pending, processing, completed, failed

    # Agreement subdocument
    agreement: Optional[dict] = None

    # Base schema fields
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    @field_validator(
        "policy_number",
        "policy_name",
        "policy_holder_name",
        "insurance_company",
        "policy_type",
        "status",
        "grid_fs_file_id",
        "original_file_name",
        "mime_type",
        "extracted_policy_text",
        mode="before",
    )
    @classmethod
    def sanitize_string_fields(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("file_size", mode="before")
    @classmethod
    def sanitize_file_size(cls, v):
        if v is None:
            return 0
        return v

    @field_validator("coverage_amount", mode="before")
    @classmethod
    def sanitize_coverage_amount(cls, v):
        if v is None:
            return 0.0
        return float(v)

    @field_validator("extracted_policy_json", mode="before")
    @classmethod
    def sanitize_dict_fields(cls, v):
        if v is None:
            return {}
        return v

    class Settings:
        name = "policies"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event([Insert, Replace, SaveChanges, Update])
    def sanitize_null_strings(self):
        """Ensure no string fields are stored as null in MongoDB."""
        string_fields = [
            "policy_number", "policy_name", "policy_holder_name",
            "insurance_company", "policy_type", "status",
            "grid_fs_file_id", "original_file_name", "mime_type",
            "extracted_policy_text"
        ]
        for field in string_fields:
            if getattr(self, field, None) is None:
                setattr(self, field, "")
        if self.file_size is None:
            self.file_size = 0
        if self.coverage_amount is None:
            self.coverage_amount = 0.0
        if self.extracted_policy_json is None:
            self.extracted_policy_json = {}

    @before_event(Insert)
    async def assign_sequence_number(self):
        """Auto-assign sequence number on insert."""
        self.sequence_number = await Counter.get_next_sequence(self.user_id, "Policy")
