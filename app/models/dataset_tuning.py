"""
Dataset, Tuning & Evaluation Data Models.
Defines schemas for:
- Dataset Types, Splits & Categories
- Training Example (Input & Expected Output)
- Dataset Manifest (Collection: kb_dataset_manifests)
- Evaluation Run (Collection: kb_evaluation_runs)
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional
from beanie import Document, Indexed
from pydantic import BaseModel, Field


class DatasetType(str, Enum):
    POLICY_KNOWLEDGE = "POLICY_KNOWLEDGE"
    EXPERT_CASES = "EXPERT_CASES"
    EXPERT_CORRECTIONS = "EXPERT_CORRECTIONS"
    EVALUATION_BENCHMARK = "EVALUATION_BENCHMARK"
    TUNING_SPLIT = "TUNING_SPLIT"


class DatasetSplitType(str, Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


class EvaluationCategory(str, Enum):
    COVERED = "COVERED"
    PERMANENT_EXCLUSION = "PERMANENT_EXCLUSION"
    WAITING_PERIOD_ACTIVE = "WAITING_PERIOD_ACTIVE"
    WAITING_PERIOD_COMPLETED = "WAITING_PERIOD_COMPLETED"
    BENEFIT_UNAVAILABLE = "BENEFIT_UNAVAILABLE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    NETWORK_MISMATCH = "NETWORK_MISMATCH"
    GEOGRAPHICAL_RESTRICTION = "GEOGRAPHICAL_RESTRICTION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    AMBIGUOUS_CASE = "AMBIGUOUS_CASE"
    CONFLICTING_RULES = "CONFLICTING_RULES"
    COMPLEX_CLAIM = "COMPLEX_CLAIM"


class EvidenceCitation(BaseModel):
    documentName: str = Field(default="Policy_Document.pdf")
    pageNumber: Optional[int] = Field(default=None)
    section: Optional[str] = Field(default="")
    clauseId: Optional[str] = Field(default="")
    textSnippet: str = Field(default="")
    authorityLevel: str = Field(default="CONTRACTUAL_POLICY_WORDING")


class TrainingExampleInput(BaseModel):
    """De-identified claim input context for model training/evaluation."""
    diagnosis: str
    normalizedDiagnosis: str
    treatment: str
    normalizedTreatment: Optional[str] = ""
    policyDurationMonths: Optional[int] = None
    insurer: str = "All"
    product: str = "Standard Benchmark"
    variant: str = "All"
    policyVersion: str = "1.0"
    applicableRules: List[str] = Field(default_factory=list)
    retrievedEvidence: List[EvidenceCitation] = Field(default_factory=list)
    hospitalType: Optional[str] = "Network"
    roomType: Optional[str] = "General"
    claimAmount: Optional[float] = None


class TrainingExpectedOutput(BaseModel):
    """Ground truth expert-approved adjudication outcome."""
    status: str  # COVERED | NOT_COVERED | PARTIALLY_COVERED | MANUAL_REVIEW
    reasonCode: str
    eligibleAmount: Optional[float] = None
    coPayPercent: Optional[float] = 0.0
    deductibleApplied: Optional[float] = 0.0
    subLimitApplied: Optional[float] = 0.0
    ruleResults: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: List[EvidenceCitation] = Field(default_factory=list)
    explanation: str
    manualReviewRequired: bool = False
    confidence: int = 100


class TrainingExample(BaseModel):
    """Complete atomic example for supervised fine-tuning or evaluation."""
    exampleId: str
    sourceCaseId: Optional[str] = None
    category: EvaluationCategory = EvaluationCategory.COVERED
    split: DatasetSplitType = DatasetSplitType.TRAIN
    input: TrainingExampleInput
    expectedOutput: TrainingExpectedOutput
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DatasetQualityGateAudit(BaseModel):
    totalScanned: int = 0
    passedCount: int = 0
    excludedCount: int = 0
    exclusionBreakdown: Dict[str, int] = Field(default_factory=dict)
    excludedCaseIds: List[Dict[str, Any]] = Field(default_factory=list)


class DatasetManifest(Document):
    """
    Immutable manifest record of an exported dataset version.
    Persisted in MongoDB (kb_dataset_manifests).
    """
    dataset_version: Indexed(str, unique=True) = Field(alias="datasetVersion")
    dataset_type: str = Field(default=DatasetType.TUNING_SPLIT.value, alias="datasetType")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    created_by: str = Field(default="system-pipeline", alias="createdBy")

    # Source & Component Versions
    source_version: str = Field(default="1.0.0", alias="sourceVersion")
    policy_knowledge_version: str = Field(default="1.0.0", alias="policyKnowledgeVersion")
    rule_engine_version: str = Field(default="2.0.0-deterministic", alias="ruleEngineVersion")
    prompt_version: str = Field(default="1.2.0", alias="promptVersion")
    model_version: str = Field(default="gemini-2.5-flash", alias="modelVersion")

    # Counts & Splits
    total_examples: int = Field(default=0, alias="totalExamples")
    train_count: int = Field(default=0, alias="trainCount")
    validation_count: int = Field(default=0, alias="validationCount")
    test_count: int = Field(default=0, alias="testCount")

    # File paths on disk
    train_file_path: Optional[str] = Field(default=None, alias="trainFilePath")
    validation_file_path: Optional[str] = Field(default=None, alias="validationFilePath")
    test_file_path: Optional[str] = Field(default=None, alias="testFilePath")
    vertex_train_file_path: Optional[str] = Field(default=None, alias="vertexTrainFilePath")
    vertex_validation_file_path: Optional[str] = Field(default=None, alias="vertexValidationFilePath")

    # Quality Gate Summary
    quality_audit: DatasetQualityGateAudit = Field(default_factory=DatasetQualityGateAudit, alias="qualityAudit")
    category_distribution: Dict[str, int] = Field(default_factory=dict, alias="categoryDistribution")

    class Settings:
        name = "kb_dataset_manifests"
        use_state_management = True
        indexes = [
            "dataset_version",
            "dataset_type",
            "created_at",
        ]

    class Config:
        populate_by_name = True


class EvaluationMetricDetail(BaseModel):
    totalCases: int = 0
    correctDecisions: int = 0
    accuracy: float = 0.0
    falseApprovals: int = 0
    falseRejections: int = 0
    manualReviews: int = 0


class EvaluationRun(Document):
    """
    Record of an evaluation benchmark execution across model/prompts.
    Persisted in MongoDB (kb_evaluation_runs).
    """
    run_id: Indexed(str, unique=True) = Field(alias="runId")
    dataset_version: Indexed(str) = Field(alias="datasetVersion")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Evaluated Model & Engine Stack
    model: str = Field(default="gemini-2.5-flash")
    model_version: str = Field(default="2.5-flash", alias="modelVersion")
    prompt_version: str = Field(default="1.2.0", alias="promptVersion")
    rule_engine_version: str = Field(default="2.0.0-deterministic", alias="ruleEngineVersion")

    # High-level summary metrics
    total_cases_evaluated: int = Field(default=0, alias="totalCasesEvaluated")
    overall_accuracy: float = Field(default=0.0, alias="overallAccuracy")
    covered_case_accuracy: float = Field(default=0.0, alias="coveredCaseAccuracy")
    exclusion_accuracy: float = Field(default=0.0, alias="exclusionAccuracy")
    waiting_period_accuracy: float = Field(default=0.0, alias="waitingPeriodAccuracy")

    # Critical Insurance Risk Metrics
    total_false_approvals: int = Field(default=0, alias="totalFalseApprovals")
    false_approval_rate: float = Field(default=0.0, alias="falseApprovalRate")
    total_false_rejections: int = Field(default=0, alias="totalFalseRejections")
    false_rejection_rate: float = Field(default=0.0, alias="falseRejectionRate")
    manual_review_rate: float = Field(default=0.0, alias="manualReviewRate")
    evidence_accuracy: float = Field(default=0.0, alias="evidenceAccuracy")
    schema_compliance_rate: float = Field(default=1.0, alias="schemaComplianceRate")

    # Granular Breakdown by Category
    category_metrics: Dict[str, EvaluationMetricDetail] = Field(default_factory=dict, alias="categoryMetrics")
    case_results: List[Dict[str, Any]] = Field(default_factory=list, alias="caseResults")
    report_file_path: Optional[str] = Field(default=None, alias="reportFilePath")

    class Settings:
        name = "kb_evaluation_runs"
        use_state_management = True
        indexes = [
            "run_id",
            "dataset_version",
            "timestamp",
            "overall_accuracy",
            "false_approval_rate",
        ]

    class Config:
        populate_by_name = True
