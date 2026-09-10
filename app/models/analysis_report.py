"""
AnalysisReport document model — migrated from AnalysisReport.js (Mongoose).
Stores the complete output of the AI analysis pipeline.
"""

from datetime import datetime, timezone
from typing import Any, Optional, Union, List

from beanie import Document, Indexed, before_event, Insert, Replace, SaveChanges, Update
from pydantic import Field, field_validator

from app.models.counter import Counter


def _sanitize_nulls(data: Any) -> Any:
    """Recursively converts None values in dictionaries and lists to empty strings or empty structures."""
    if isinstance(data, dict):
        return {k: _sanitize_nulls(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_nulls(item) for item in data]
    elif data is None:
        return ""
    return data


class AnalysisReport(Document):
    """Complete analysis pipeline output stored in MongoDB."""

    # ─── Ownership ────────────────────────────────────────────────────────────
    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    policy_id: Optional[str] = Field(default="", alias="policyId")
    prescription_id: Optional[str] = Field(default="", alias="prescriptionId")
    session_id: Optional[str] = Field(default="", alias="sessionId")
    report_number: Optional[int] = Field(default=None, alias="reportNumber")

    # ─── Pipeline Status ──────────────────────────────────────────────────────
    status: str = Field(default="pending")  # pending | extracting | analyzing | completed | failed | manual_review_required
    analysis_version: str = Field(default="2.1.0", alias="analysisVersion")
    decision_type: Optional[str] = Field(default="Automatic", alias="decisionType") # Automatic | Manual Review
    confidence_score: Optional[int] = Field(default=0, alias="confidenceScore")
    processing_time_ms: Optional[int] = Field(default=0, alias="processingTimeMs")

    # ─── Decision & Core Outcomes ─────────────────────────────────────────────
    overall_status: Optional[str] = Field(default="", alias="overallStatus")
    summary: Optional[str] = Field(default="")
    dominance_score: Optional[float] = Field(default=0.0, alias="dominanceScore")
    coverage_breakdown: Optional[dict] = Field(default_factory=dict, alias="coverageBreakdown")
    comparison: Optional[List[dict]] = Field(default_factory=list)

    # ─── Deep Adjudication & Rules ────────────────────────────────────────────
    coverage_analysis: Optional[dict] = Field(default_factory=dict, alias="coverageAnalysis")
    business_rules: Optional[dict] = Field(default_factory=dict, alias="businessRules")
    document_validity: Optional[dict] = Field(default_factory=dict, alias="documentValidity")

    # ─── Snapshot Caches ──────────────────────────────────────────────────────
    policy_json: Optional[dict] = Field(default_factory=dict, alias="policyJson")
    prescription_json: Optional[dict] = Field(default_factory=dict, alias="prescriptionJson")
    policy_text: Optional[str] = Field(default="", alias="policyText")
    prescription_text: Optional[str] = Field(default="", alias="prescriptionText")

    # ─── Evidence & Audit Trail ───────────────────────────────────────────────
    policy_clauses_used: Optional[List[Any]] = Field(default_factory=list, alias="policyClausesUsed")
    prescription_evidence: Optional[List[Any]] = Field(default_factory=list, alias="prescriptionEvidence")
    clarification_answers_used: Optional[List[Any]] = Field(default_factory=list, alias="clarificationAnswersUsed")

    # ─── Error Info ───────────────────────────────────────────────────────────
    error_message: Optional[str] = Field(default="", alias="errorMessage")
    failed_at_stage: Optional[str] = Field(default="", alias="failedAtStage")

    # ─── Base Schema Timestamps ───────────────────────────────────────────────
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    @field_validator(
        "policy_id",
        "prescription_id",
        "session_id",
        "status",
        "analysis_version",
        "decision_type",
        "overall_status",
        "summary",
        "policy_text",
        "prescription_text",
        "error_message",
        "failed_at_stage",
        mode="before",
    )
    @classmethod
    def sanitize_string_fields(cls, v):
        if v is None:
            return ""
        return str(v)

    @field_validator("dominance_score", mode="before")
    @classmethod
    def sanitize_dominance_score(cls, v):
        if v is None:
            return 0.0
        return float(v)

    @field_validator("confidence_score", "processing_time_ms", mode="before")
    @classmethod
    def sanitize_int_fields(cls, v):
        if v is None:
            return 0
        return int(v)

    @field_validator(
        "coverage_breakdown",
        "coverage_analysis",
        "business_rules",
        "document_validity",
        "policy_json",
        "prescription_json",
        mode="before",
    )
    @classmethod
    def sanitize_dict_fields(cls, v):
        if v is None:
            return {}
        if isinstance(v, dict):
            return _sanitize_nulls(v)
        return v

    @field_validator(
        "comparison",
        "policy_clauses_used",
        "prescription_evidence",
        "clarification_answers_used",
        mode="before",
    )
    @classmethod
    def sanitize_list_fields(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [_sanitize_nulls(item) for item in v]
        return v

    class Settings:
        name = "analysisreports"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event([Insert, Replace, SaveChanges, Update])
    def sanitize_null_fields(self):
        """Ensure no string, dict, or list fields are stored as null in MongoDB."""
        string_fields = [
            "policy_id", "prescription_id", "session_id", "status",
            "analysis_version", "decision_type", "overall_status",
            "summary", "policy_text", "prescription_text",
            "error_message", "failed_at_stage"
        ]
        for field in string_fields:
            if getattr(self, field, None) is None:
                setattr(self, field, "")
        if self.dominance_score is None:
            self.dominance_score = 0.0
        if self.confidence_score is None:
            self.confidence_score = 0
        if self.processing_time_ms is None:
            self.processing_time_ms = 0

        dict_fields = [
            "coverage_breakdown", "coverage_analysis", "business_rules",
            "document_validity", "policy_json", "prescription_json"
        ]
        for field in dict_fields:
            val = getattr(self, field, None)
            if val is None:
                setattr(self, field, {})
            else:
                setattr(self, field, _sanitize_nulls(val))

        list_fields = [
            "comparison", "policy_clauses_used",
            "prescription_evidence", "clarification_answers_used"
        ]
        for field in list_fields:
            val = getattr(self, field, None)
            if val is None:
                setattr(self, field, [])
            else:
                setattr(self, field, [_sanitize_nulls(item) for item in val])

    @before_event(Insert)
    async def assign_report_number(self):
        """Auto-assign report number on insert."""
        self.report_number = await Counter.get_next_sequence(self.user_id, "AnalysisReport")
