"""
Policy document model — migrated from Policy.js (Mongoose).
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, Indexed, before_event, Insert
from pydantic import Field

from app.models.counter import Counter



class Policy(Document):
    """Insurance policy document stored in MongoDB."""

    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    policy_number: Optional[str] = Field(default=None, alias="policyNumber", max_length=255)
    policy_name: Optional[str] = Field(default=None, alias="policyName", max_length=255)
    policy_holder_name: Optional[str] = Field(default=None, alias="policyHolderName", max_length=255)
    insurance_company: Optional[str] = Field(default=None, alias="insuranceCompany", max_length=255)
    policy_type: Optional[str] = Field(default=None, alias="policyType", max_length=255)
    policy_start_date: Optional[datetime] = Field(default=None, alias="policyStartDate")
    policy_end_date: Optional[datetime] = Field(default=None, alias="policyEndDate")
    coverage_amount: Optional[float] = Field(default=None, alias="coverageAmount", ge=0)
    status: str = Field(default="Active", max_length=50)
    grid_fs_file_id: Optional[str] = Field(default=None, alias="gridFsFileId")
    original_file_name: Optional[str] = Field(default=None, alias="originalFileName", max_length=255)
    mime_type: Optional[str] = Field(default=None, alias="mimeType", max_length=255)
    file_size: Optional[int] = Field(default=None, alias="fileSize", ge=0)
    sequence_number: Optional[int] = Field(default=None, alias="sequenceNumber", ge=0)

    # Extraction Data
    extracted_policy_text: Optional[str] = Field(default=None, alias="extractedPolicyText")
    extracted_policy_json: Optional[dict] = Field(default=None, alias="extractedPolicyJson")
    metadata: Optional[dict] = Field(default=None, alias="metadata")
    processing_status: str = Field(default="pending", alias="processingStatus") # pending, processing, completed, failed

    # Agreement subdocument
    agreement: Optional[dict] = None

    # Base schema fields
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default=None, alias="createdBy")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")
    is_deleted: bool = Field(default=False, alias="isDeleted")

    class Settings:
        name = "policies"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event(Insert)
    async def assign_sequence_number(self):
        """Auto-assign sequence number on insert."""
        self.sequence_number = await Counter.get_next_sequence(self.user_id, "Policy")
