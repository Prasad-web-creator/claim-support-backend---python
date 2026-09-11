"""
Data Models and Schemas for the Deterministic Insurance Coverage Rule Engine.
Defines rule types, status codes, evidence citations, normalized clinical entities,
financial impact breakdowns, and audit trails.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class RuleType(str, Enum):
    POLICY_VALIDITY = "POLICY_VALIDITY"
    PERMANENT_EXCLUSION = "PERMANENT_EXCLUSION"
    WAITING_PERIOD = "WAITING_PERIOD"
    BENEFIT = "BENEFIT"
    LIMIT = "LIMIT"
    SUB_LIMIT = "SUB_LIMIT"
    CO_PAY = "CO_PAY"
    DEDUCTIBLE = "DEDUCTIBLE"
    NETWORK = "NETWORK"
    NETWORK_REQUIREMENT = "NETWORK_REQUIREMENT"
    GEOGRAPHICAL = "GEOGRAPHICAL"
    GEOGRAPHICAL_RESTRICTION = "GEOGRAPHICAL_RESTRICTION"
    HOSPITALIZATION = "HOSPITALIZATION"
    HOSPITALIZATION_REQUIREMENT = "HOSPITALIZATION_REQUIREMENT"
    PROVIDER = "PROVIDER"
    OTHER_POLICY_SPECIFIC = "OTHER_POLICY_SPECIFIC"


class RuleStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    FLAGGED = "FLAGGED"


class ReasonCode(str, Enum):
    POLICY_ACTIVE = "POLICY_ACTIVE"
    POLICY_EXPIRED = "POLICY_EXPIRED"
    POLICY_NOT_YET_EFFECTIVE = "POLICY_NOT_YET_EFFECTIVE"
    FUTURE_TREATMENT_DATE = "FUTURE_TREATMENT_DATE"
    MISSING_POLICY_DATES = "MISSING_POLICY_DATES"
    PERMANENT_EXCLUSION = "PERMANENT_EXCLUSION"
    WAITING_PERIOD_ACTIVE = "WAITING_PERIOD_ACTIVE"
    WAITING_PERIOD_COMPLETED = "WAITING_PERIOD_COMPLETED"
    BENEFIT_COVERED = "BENEFIT_COVERED"
    BENEFIT_NOT_INCLUDED = "BENEFIT_NOT_INCLUDED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    WITHIN_LIMITS = "WITHIN_LIMITS"
    COPAY_APPLICABLE = "COPAY_APPLICABLE"
    DEDUCTIBLE_APPLICABLE = "DEDUCTIBLE_APPLICABLE"
    NETWORK_RESTRICTION = "NETWORK_RESTRICTION"
    GEOGRAPHIC_RESTRICTION = "GEOGRAPHIC_RESTRICTION"
    HOSPITALIZATION_CRITERIA_NOT_MET = "HOSPITALIZATION_CRITERIA_NOT_MET"
    AMBIGUOUS_DIAGNOSIS = "AMBIGUOUS_DIAGNOSIS"
    AMBIGUOUS_POLICY_TERMS = "AMBIGUOUS_POLICY_TERMS"
    CONFLICTING_RULES = "CONFLICTING_RULES"


class DecisionStatus(str, Enum):
    COVERED = "COVERED"
    NOT_COVERED = "NOT_COVERED"
    NOT_CURRENTLY_COVERED = "NOT_CURRENTLY_COVERED"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class RuleEvidence(BaseModel):
    """Auditable citation linking a rule result to source policy document and page."""
    document_id: Optional[str] = Field(default="", alias="documentId")
    source_document: str = Field(alias="sourceDocument")
    page: Optional[int] = None
    section: Optional[str] = ""
    clause_id: Optional[str] = Field(default="", alias="clauseId")
    text_snippet: Optional[str] = Field(default="", alias="textSnippet")
    authority_level: str = Field(default="CONTRACTUAL_POLICY_WORDING", alias="authorityLevel")

    class Config:
        populate_by_name = True


class RuleCheckResult(BaseModel):
    """Detailed evaluation result for an individual deterministic rule."""
    rule_type: RuleType = Field(alias="ruleType")
    rule_id: Optional[str] = Field(default="", alias="ruleId")
    condition: Optional[str] = None
    required_months: Optional[int] = Field(default=None, alias="requiredMonths")
    completed_months: Optional[int] = Field(default=None, alias="completedMonths")
    status: RuleStatus
    reason_code: ReasonCode = Field(alias="reasonCode")
    description: str
    evidence: Optional[RuleEvidence] = None
    financial_impact: Optional[Dict[str, Any]] = Field(default=None, alias="financialImpact")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    class Config:
        populate_by_name = True


class NormalizedCondition(BaseModel):
    """Clinical entity extracted and mapped to canonical insurance vocabulary."""
    original_text: str = Field(alias="originalText")
    normalized_value: str = Field(alias="normalizedValue")
    confidence: float = 1.0
    source: str = "diagnosis"  # diagnosis, procedure, symptom, medicine
    category: str = "GENERAL"   # SURGICAL, MEDICAL, COSMETIC, CHRONIC, OPHTHALMIC, GENERAL
    matched_keywords: List[str] = Field(default_factory=list, alias="matchedKeywords")

    class Config:
        populate_by_name = True


class FinancialImpact(BaseModel):
    """Financial calculation breakdown resulting from deductible, co-pay, and sub-limits."""
    claimed_amount: Optional[float] = Field(default=None, alias="claimedAmount")
    eligible_amount: Optional[float] = Field(default=None, alias="eligibleAmount")
    sub_limit_applied: Optional[float] = Field(default=None, alias="subLimitApplied")
    copay_pct: Optional[float] = Field(default=None, alias="copayPct")
    copay_amount: Optional[float] = Field(default=None, alias="copayAmount")
    deductible_amount: Optional[float] = Field(default=None, alias="deductibleAmount")
    non_payable_amount: Optional[float] = Field(default=0.0, alias="nonPayableAmount")
    notes: List[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class RuleAuditTrail(BaseModel):
    """Audit metadata tracking rule evaluations for regulatory compliance."""
    engine_version: str = Field(default="2.0.0-deterministic", alias="engineVersion")
    rule_ids_evaluated: List[str] = Field(default_factory=list, alias="ruleIdsEvaluated")
    rules_matched: List[str] = Field(default_factory=list, alias="rulesMatched")
    rules_failed: List[str] = Field(default_factory=list, alias="rulesFailed")
    rule_versions: Dict[str, str] = Field(default_factory=dict, alias="ruleVersions")
    policy_version: str = Field(default="1.0", alias="policyVersion")
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="evaluatedAt")

    class Config:
        populate_by_name = True


class DeterministicDecision(BaseModel):
    """Overall outcome of the deterministic rule engine evaluation."""
    status: DecisionStatus
    reason_code: ReasonCode = Field(alias="reasonCode")
    manual_review_required: bool = Field(default=False, alias="manualReviewRequired")
    overall_eligible: bool = Field(default=True, alias="overallEligible")
    blockers: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    financials: FinancialImpact = Field(default_factory=FinancialImpact)
    normalized_conditions: List[NormalizedCondition] = Field(default_factory=list, alias="normalizedConditions")
    rule_results: List[RuleCheckResult] = Field(default_factory=list, alias="ruleResults")
    audit: RuleAuditTrail = Field(default_factory=RuleAuditTrail)

    class Config:
        populate_by_name = True
