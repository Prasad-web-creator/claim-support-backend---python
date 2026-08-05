"""
AnalysisReport document model — migrated from AnalysisReport.js (Mongoose).
Stores the complete output of the 11-stage AI analysis pipeline.
"""

from datetime import datetime
from typing import Any, Optional

from beanie import Document, Indexed, before_event, Insert
from pydantic import Field

from app.models.counter import Counter


class AnalysisReport(Document):
    """Complete analysis pipeline output stored in MongoDB."""

    # ─── Ownership ────────────────────────────────────────────────────────────
    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]
    policy_id: Optional[str] = Field(default=None, alias="policyId")
    prescription_id: Optional[str] = Field(default=None, alias="prescriptionId")

    # ─── Pipeline Status ──────────────────────────────────────────────────────
    status: str = Field(default="pending")  # pending | extracting | analyzing | completed | failed
    analysis_version: str = Field(default="2.0.0", alias="analysisVersion")

    # ─── Stage 4: Extracted Policy JSON ───────────────────────────────────────
    policy_json: Optional[dict] = Field(default=None, alias="policyJson")
    policy_metadata: Optional[dict] = Field(default=None, alias="policyMetadata")

    # ─── Stage 5: Extracted Prescription JSON ─────────────────────────────────
    prescription_json: Optional[dict] = Field(default=None, alias="prescriptionJson")
    prescription_metadata: Optional[dict] = Field(default=None, alias="prescriptionMetadata")

    # ─── Stage 7: Business Rule Engine Results ────────────────────────────────
    business_rules: Optional[dict] = Field(default=None, alias="businessRules")

    # ─── Stage 8: AI Coverage Analysis & Document Validity ────────────────────
    document_validity: Optional[dict] = Field(default=None, alias="documentValidity")
    overall_status: Optional[str] = Field(default=None, alias="overallStatus")
    summary: Optional[str] = None

    coverage_analysis: Optional[dict] = Field(default=None, alias="coverageAnalysis")

    # ─── Audit Trail ──────────────────────────────────────────────────────────
    policy_text: Optional[str] = Field(default=None, alias="policyText")
    prescription_text: Optional[str] = Field(default=None, alias="prescriptionText")

    # ─── Summary & Detailed Comparison ────────────────────────────────────────
    dominance_score: Optional[float] = Field(default=None, alias="dominanceScore")
    coverage_breakdown: Optional[dict] = Field(default=None, alias="coverageBreakdown")
    summary_text: Optional[str] = Field(default=None, alias="summaryText")
    comparison: Optional[list[dict]] = Field(default=None)

    # ─── Processing Metadata ──────────────────────────────────────────────────
    processing_time_ms: Optional[int] = Field(default=None, alias="processingTimeMs")
    stages: Optional[list[dict]] = None

    # ─── Error Info ───────────────────────────────────────────────────────────
    error_message: Optional[str] = Field(default=None, alias="errorMessage")
    failed_at_stage: Optional[str] = Field(default=None, alias="failedAtStage")

    report_number: Optional[int] = Field(default=None, alias="reportNumber")

    # Base schema fields
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow, alias="createdAt")
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow, alias="updatedAt")
    created_by: Optional[str] = Field(default=None, alias="createdBy")
    updated_by: Optional[str] = Field(default=None, alias="updatedBy")
    is_deleted: bool = Field(default=False, alias="isDeleted")

    class Settings:
        name = "analysisreports"
        use_state_management = True

    class Config:
        populate_by_name = True

    @before_event(Insert)
    async def assign_report_number(self):
        """Auto-assign report number on insert."""
        self.report_number = await Counter.get_next_sequence(self.user_id, "AnalysisReport")
