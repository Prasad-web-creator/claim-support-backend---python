"""
Expert-Reviewed Claim History and Learning Data Models.
Defines MongoDB collections via Beanie ODM for historical claim cases,
analysis versions, expert review actions, expert corrections, and curated
de-identified approved expert cases with vector embedding metadata.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any

from beanie import Document, Indexed
from pydantic import BaseModel, Field


class ExpertReviewStatus(str, Enum):
    PENDING = "PENDING"
    PENDING_EXPERT_REVIEW = "PENDING_EXPERT_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ExpertAction(str, Enum):
    ACCEPT = "ACCEPT"
    CORRECT = "CORRECT"
    REJECT = "REJECT"


class CaseEvidence(BaseModel):
    """Auditable evidence chunk preserved in expert historical cases."""
    document_name: str = Field(alias="documentName")
    page_number: Optional[int] = Field(default=None, alias="pageNumber")
    section: Optional[str] = ""
    clause_id: Optional[str] = Field(default="", alias="clauseId")
    text_snippet: Optional[str] = Field(default="", alias="textSnippet")
    authority_level: str = Field(default="CONTRACTUAL_POLICY_WORDING", alias="authorityLevel")

    class Config:
        populate_by_name = True


class CaseEmbeddingMetadata(BaseModel):
    """Metadata detailing the vector embedding generated for a case."""
    semantic_text: str = Field(alias="semanticText")
    model_name: str = Field(default="local-semantic-v1", alias="modelName")
    dimension: int = 128
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 1. Claim Case Model (Primary Case Record)
# ──────────────────────────────────────────────────────────────────────────────

class ClaimCase(Document):
    """
    Primary historical record of an analyzed claim.
    Transitions from PENDING_EXPERT_REVIEW to APPROVED, REJECTED, or SUPERSEDED.
    """
    case_id: Indexed(str, unique=True) = Field(alias="caseId")
    analysis_session_id: Optional[str] = Field(default="", alias="analysisSessionId")
    report_id: Optional[str] = Field(default="", alias="reportId")
    policy_id: Indexed(str) = Field(default="", alias="policyId")
    insurer: Indexed(str) = Field(default="All")
    product: Indexed(str) = Field(default="Standard Benchmark")
    variant: Indexed(str) = Field(default="All")
    policy_version: Indexed(str) = Field(default="1.0", alias="policyVersion")

    # Clinical details
    diagnosis: str
    normalized_diagnosis: Indexed(str) = Field(alias="normalizedDiagnosis")
    treatment: str
    normalized_treatment: Optional[str] = Field(default="", alias="normalizedTreatment")
    treatment_date: Optional[datetime] = Field(default=None, alias="treatmentDate")
    policy_start_date: Optional[datetime] = Field(default=None, alias="policyStartDate")
    policy_age_months: Optional[int] = Field(default=None, alias="policyAgeMonths")
    relevant_rule_ids: List[str] = Field(default_factory=list, alias="relevantRuleIds")

    # Initial AI & Rule outcomes
    ai_decision: str = Field(default="COVERED", alias="aiDecision")
    ai_explanation: Optional[str] = Field(default="", alias="aiExplanation")
    deterministic_decision: str = Field(default="COVERED", alias="deterministicDecision")
    deterministic_reason_code: str = Field(default="POLICY_ACTIVE", alias="deterministicReasonCode")

    # Final Expert outcomes
    final_expert_decision: Optional[str] = Field(default=None, alias="finalExpertDecision")
    reason_code: Optional[str] = Field(default="", alias="reasonCode")
    explanation: Optional[str] = Field(default="", alias="explanation")
    expert_status: Indexed(str) = Field(default=ExpertReviewStatus.PENDING_EXPERT_REVIEW.value, alias="expertStatus")
    is_approved_for_knowledge: bool = Field(default=False, alias="isApprovedForKnowledge")

    # Evidence & Versioning
    evidence: List[CaseEvidence] = Field(default_factory=list)
    model_version: str = Field(default="gemini-2.5-flash", alias="modelVersion")
    prompt_version: str = Field(default="1.2.0", alias="promptVersion")
    rule_engine_version: str = Field(default="2.0.0-deterministic", alias="ruleEngineVersion")
    knowledge_base_version: str = Field(default="1.0.0", alias="knowledgeBaseVersion")
    analysis_version: str = Field(default="2.1.0", alias="analysisVersion")
    dataset_version: str = Field(default="1.0.0", alias="datasetVersion")

    # Audit timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    class Settings:
        name = "kb_claim_cases"
        use_state_management = True
        indexes = [
            "case_id",
            "policy_id",
            "insurer",
            "product",
            "variant",
            "policy_version",
            "normalized_diagnosis",
            "expert_status",
            "created_at",
            [("insurer", 1), ("product", 1), ("expert_status", 1)],
            [("normalized_diagnosis", 1), ("expert_status", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 2. Claim Analysis Version Model (Immutable Historical Snapshot)
# ──────────────────────────────────────────────────────────────────────────────

class ClaimAnalysisVersion(Document):
    """
    Immutable audit snapshot capturing each analysis execution for a claim case.
    Preserves original AI and deterministic findings before any expert edits.
    """
    version_id: Indexed(str, unique=True) = Field(alias="versionId")
    case_id: Indexed(str) = Field(alias="caseId")
    version_number: int = Field(default=1, alias="versionNumber")
    ai_decision: str = Field(alias="aiDecision")
    deterministic_decision: str = Field(alias="deterministicDecision")
    deterministic_reason_code: str = Field(alias="deterministicReasonCode")
    confidence_score: Optional[float] = Field(default=None, alias="confidenceScore")
    explanation: Optional[str] = Field(default="")
    rule_results: List[Dict[str, Any]] = Field(default_factory=list, alias="ruleResults")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")

    class Settings:
        name = "kb_claim_analysis_versions"
        use_state_management = True
        indexes = [
            "version_id",
            "case_id",
            "version_number",
            [("case_id", 1), ("version_number", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 3. Expert Review Model (Review Action Log)
# ──────────────────────────────────────────────────────────────────────────────

class ExpertReview(Document):
    """
    Audit record of an individual review event performed by a medical/insurance expert.
    """
    review_id: Indexed(str, unique=True) = Field(alias="reviewId")
    case_id: Indexed(str) = Field(alias="caseId")
    expert_id: Indexed(str) = Field(default="expert-reviewer", alias="expertId")
    action: str = Field(alias="action")  # ACCEPT | CORRECT | REJECT
    comment: Optional[str] = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")

    class Settings:
        name = "kb_expert_reviews"
        use_state_management = True
        indexes = [
            "review_id",
            "case_id",
            "expert_id",
            "action",
            "created_at",
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 4. Expert Correction Model (Correction Delta & Rationale)
# ──────────────────────────────────────────────────────────────────────────────

class ExpertCorrection(Document):
    """
    Stores the exact delta when an expert corrects an initial AI or rule decision.
    Preserves both original AI output and corrected expert verdict for auditing.
    """
    correction_id: Indexed(str, unique=True) = Field(alias="correctionId")
    case_id: Indexed(str) = Field(alias="caseId")
    review_id: Indexed(str) = Field(alias="reviewId")

    original_ai_decision: str = Field(alias="originalAiDecision")
    original_deterministic_decision: str = Field(alias="originalDeterministicDecision")
    original_reason_code: Optional[str] = Field(default="", alias="originalReasonCode")

    corrected_decision: str = Field(alias="correctedDecision")
    corrected_reason_code: str = Field(alias="correctedReasonCode")
    correction_reason: str = Field(alias="correctionReason")
    expert_comment: Optional[str] = Field(default="", alias="expertComment")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "kb_expert_corrections"
        use_state_management = True
        indexes = [
            "correction_id",
            "case_id",
            "review_id",
            "timestamp",
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 5. Approved Expert Case Model (Trusted De-Identified Knowledge Base)
# ──────────────────────────────────────────────────────────────────────────────

class ApprovedExpertCase(Document):
    """
    Curated, verified, de-identified expert knowledge record available for similar-case retrieval.
    Contains strictly zero PII (no patient name, phone, email, policy number, or member ID).
    """
    approved_case_id: Indexed(str, unique=True) = Field(alias="approvedCaseId")
    case_id: Indexed(str) = Field(alias="caseId")
    insurer: Indexed(str)
    product: Indexed(str)
    variant: Indexed(str) = Field(default="All")
    policy_version: Indexed(str) = Field(default="1.0", alias="policyVersion")
    dataset_version: Indexed(str) = Field(default="1.0.0", alias="datasetVersion")

    # De-identified clinical parameters
    diagnosis: str
    normalized_diagnosis: Indexed(str) = Field(alias="normalizedDiagnosis")
    treatment: str
    normalized_treatment: Indexed(str) = Field(default="", alias="normalizedTreatment")
    policy_age_months: Optional[int] = Field(default=None, alias="policyAgeMonths")
    applicable_rule_types: List[str] = Field(default_factory=list, alias="applicableRuleTypes")
    relevant_rule_ids: List[str] = Field(default_factory=list, alias="relevantRuleIds")

    # Expert verdict & explanation
    decision: Indexed(str)  # COVERED | NOT_COVERED | PARTIALLY_COVERED | MANUAL_REVIEW
    reason_code: Indexed(str) = Field(alias="reasonCode")
    expert_explanation: str = Field(alias="expertExplanation")
    evidence: List[CaseEvidence] = Field(default_factory=list)

    # Retrieval status & vector embedding
    status: Indexed(str) = Field(default=ExpertReviewStatus.APPROVED.value)
    is_active: bool = Field(default=True, alias="isActive")
    superseded_case_id: Optional[str] = Field(default=None, alias="supersededCaseId")
    embedding: Optional[List[float]] = None
    embedding_metadata: Optional[CaseEmbeddingMetadata] = Field(default=None, alias="embeddingMetadata")

    # Audit timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")

    class Settings:
        name = "kb_approved_expert_cases"
        use_state_management = True
        indexes = [
            "approved_case_id",
            "case_id",
            "insurer",
            "product",
            "variant",
            "policy_version",
            "normalized_diagnosis",
            "normalized_treatment",
            "decision",
            "reason_code",
            "status",
            "is_active",
            [("insurer", 1), ("product", 1), ("variant", 1), ("status", 1)],
            [("normalized_diagnosis", 1), ("status", 1), ("is_active", 1)],
        ]

    class Config:
        populate_by_name = True
