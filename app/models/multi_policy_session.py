"""
MultiPolicyAnalysisSession document model.

Parent record for analysing ONE prescription against N policies. It owns the
shared prescription extraction and fans out to one ordinary AnalysisSession per
policy, so every policy keeps its own status, clarification state and report.
"""

from datetime import datetime, timezone
from typing import Any, Optional, List

from beanie import Document, Indexed, before_event, Insert, Replace, SaveChanges, Update
from pydantic import Field, BaseModel, field_validator


def _sanitize_nulls(data: Any) -> Any:
    """Recursively converts None values in dictionaries and lists to empty strings or empty structures."""
    if isinstance(data, dict):
        return {k: _sanitize_nulls(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_nulls(item) for item in data]
    elif data is None:
        return ""
    return data


class PolicyAnalysisEntry(BaseModel):
    """
    One policy's slot inside a multi-policy session.

    `session_id` points at the child AnalysisSession that actually runs the
    existing single-policy pipeline for this policy, so every result stays
    independently traceable as (parent, prescription, policy, session, report).
    """

    policy_id: str = Field(default="", alias="policyId")
    policy_name: Optional[str] = Field(default="", alias="policyName")
    insurance_company: Optional[str] = Field(default="", alias="insuranceCompany")

    session_id: Optional[str] = Field(default="", alias="sessionId")
    report_id: Optional[str] = Field(default="", alias="reportId")

    # Mirrors the AnalysisSession status vocabulary, plus "queued" before it starts.
    status: str = Field(default="queued")
    overall_status: Optional[str] = Field(default="", alias="overallStatus")
    decision_type: Optional[str] = Field(default="", alias="decisionType")
    dominance_score: Optional[float] = Field(default=0.0, alias="dominanceScore")

    questions: List[dict] = Field(default_factory=list)
    error_message: Optional[str] = Field(default="", alias="errorMessage")
    failed_at_stage: Optional[str] = Field(default="", alias="failedAtStage")

    started_at: Optional[datetime] = Field(default=None, alias="startedAt")
    completed_at: Optional[datetime] = Field(default=None, alias="completedAt")

    class Config:
        populate_by_name = True


class MultiPolicyAnalysisSession(Document):
    """Parent session: one prescription analysed against one or more policies."""

    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    prescription_id: Optional[str] = Field(default="", alias="prescriptionId")

    # queued | extracting | analyzing | waiting_for_user | completed | partial | failed
    status: str = Field(default="queued")

    # Shared prescription extraction — done ONCE and reused by every policy.
    prescription_text: Optional[str] = Field(default="", alias="prescriptionText")
    prescription_json: Optional[dict] = Field(default_factory=dict, alias="prescriptionJson")
    is_manual_prescription: bool = Field(default=False, alias="isManualPrescription")

    policy_analyses: List[PolicyAnalysisEntry] = Field(default_factory=list, alias="policyAnalyses")
    comparison_summary: Optional[dict] = Field(default_factory=dict, alias="comparisonSummary")

    total_processing_time_ms: int = Field(default=0, alias="totalProcessingTimeMs")
    error_message: Optional[str] = Field(default="", alias="errorMessage")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    @field_validator("prescription_id", "status", "prescription_text", "error_message", mode="before")
    @classmethod
    def sanitize_string_fields(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("prescription_json", "comparison_summary", mode="before")
    @classmethod
    def sanitize_dict_fields(cls, v):
        if v is None:
            return {}
        if isinstance(v, dict):
            return _sanitize_nulls(v)
        return v

    def get_entry(self, policy_id: str) -> Optional[PolicyAnalysisEntry]:
        """Find this session's slot for a given policy id."""
        for entry in self.policy_analyses:
            if entry.policy_id == policy_id:
                return entry
        return None

    def get_entry_by_session(self, session_id: str) -> Optional[PolicyAnalysisEntry]:
        """Find this session's slot for a given child AnalysisSession id."""
        for entry in self.policy_analyses:
            if entry.session_id == session_id:
                return entry
        return None

    class Settings:
        name = "multipolicyanalysissessions"
        use_state_management = True
        indexes = [
            "user_id",
            "prescription_id",
            "created_at",
        ]

    class Config:
        populate_by_name = True

    @before_event([Insert, Replace, SaveChanges, Update])
    def sanitize_null_fields(self):
        """Ensure no string or dictionary fields are stored as null in MongoDB."""
        string_fields = ["prescription_id", "status", "prescription_text", "error_message"]
        for field_name in string_fields:
            if getattr(self, field_name, None) is None:
                setattr(self, field_name, "")
        if self.prescription_json is None:
            self.prescription_json = {}
        else:
            self.prescription_json = _sanitize_nulls(self.prescription_json)
        if self.comparison_summary is None:
            self.comparison_summary = {}
        self.updated_at = datetime.now(timezone.utc)
