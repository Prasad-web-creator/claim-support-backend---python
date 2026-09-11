"""
Insurance Policy Knowledge Base Document Models.
Stores structured, versioned, data-driven insurance product specifications,
rules, clauses, and evidence references independently from claim records.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from enum import Enum

from beanie import Document, Indexed, before_event, Insert, Replace, SaveChanges, Update
from pydantic import Field, field_validator


class RuleType(str, Enum):
    POLICY_VALIDITY = "POLICY_VALIDITY"
    WAITING_PERIOD = "WAITING_PERIOD"
    PERMANENT_EXCLUSION = "PERMANENT_EXCLUSION"
    BENEFIT = "BENEFIT"
    OPTIONAL_BENEFIT = "OPTIONAL_BENEFIT"
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


class AuthorityLevel(str, Enum):
    REFERENCE_BENCHMARK = "REFERENCE_BENCHMARK"
    MARKETING_BROCHURE = "MARKETING_BROCHURE"
    CONTRACTUAL_POLICY_WORDING = "CONTRACTUAL_POLICY_WORDING"


class DecisionOutcome(str, Enum):
    COVERED = "COVERED"
    NOT_COVERED = "NOT_COVERED"
    CONDITIONAL = "CONDITIONAL"
    SUBJECT_TO_WAITING_PERIOD = "SUBJECT_TO_WAITING_PERIOD"
    SUBJECT_TO_LIMIT = "SUBJECT_TO_LIMIT"


# ──────────────────────────────────────────────────────────────────────────────
# 1. Insurance Company Model
# ──────────────────────────────────────────────────────────────────────────────

class InsuranceCompany(Document):
    """Registered insurance company / insurer in the Knowledge Base."""

    company_id: Indexed(str, unique=True) = Field(alias="companyId")
    name: Indexed(str, unique=True)
    code: Optional[str] = Field(default="", max_length=50)
    category: str = Field(default="Private General Insurer")  # SAHI, Private General, Public General
    market_rank: Optional[int] = Field(default=None, alias="marketRank")
    market_share_pct: Optional[float] = Field(default=None, alias="marketSharePct")
    solvency_ratio: Optional[float] = Field(default=None, alias="solvencyRatio")
    solvency_status: Optional[str] = Field(default="Healthy", alias="solvencyStatus")
    is_active: bool = Field(default=True, alias="isActive")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    class Settings:
        name = "kb_insurance_companies"
        use_state_management = True
        indexes = [
            "company_id",
            "name",
            "category",
            "is_active",
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 2. Insurance Product Model
# ──────────────────────────────────────────────────────────────────────────────

class InsuranceProduct(Document):
    """Insurance product specification (e.g. ReAssure 3.0)."""

    product_id: Indexed(str, unique=True) = Field(alias="productId")
    company_name: Indexed(str) = Field(alias="companyName")
    name: Indexed(str)
    product_type: str = Field(default="Comprehensive Health Insurance", alias="productType")
    description: Optional[str] = Field(default="")
    current_version: str = Field(default="1.0", alias="currentVersion")
    is_active: bool = Field(default=True, alias="isActive")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    class Settings:
        name = "kb_insurance_products"
        use_state_management = True
        indexes = [
            "product_id",
            "name",
            "company_name",
            "is_active",
            [("company_name", 1), ("name", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 3. Product Variant / Plan Model
# ──────────────────────────────────────────────────────────────────────────────

class ProductVariant(Document):
    """Specific variant/plan tier of a product (e.g. Classic, Select, Elite, Black)."""

    variant_id: Indexed(str, unique=True) = Field(alias="variantId")
    product_id: Indexed(str) = Field(alias="productId")
    product_name: Indexed(str) = Field(alias="productName")
    company_name: Indexed(str) = Field(alias="companyName")
    name: Indexed(str)  # Classic, Select, Elite, Black
    description: Optional[str] = Field(default="")

    # Plan-specific default parameters
    room_category: Optional[str] = Field(default="", alias="roomCategory")
    road_ambulance_limit: Optional[str] = Field(default="", alias="roadAmbulanceLimit")
    air_ambulance_limit: Optional[str] = Field(default="", alias="airAmbulanceLimit")
    modern_treatment_limit: Optional[str] = Field(default="", alias="modernTreatmentLimit")
    lock_the_clock: bool = Field(default=False, alias="lockTheClock")
    personal_accident_optional: Optional[str] = Field(default="", alias="personalAccidentOptional")
    hospital_daily_cash_optional: Optional[str] = Field(default="", alias="hospitalDailyCashOptional")
    borderless_optional: Optional[str] = Field(default="", alias="borderlessOptional")
    is_active: bool = Field(default=True, alias="isActive")
    features: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    class Settings:
        name = "kb_product_variants"
        use_state_management = True
        indexes = [
            "variant_id",
            "product_id",
            "name",
            "is_active",
            [("product_name", 1), ("name", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 4. Policy Version Model
# ──────────────────────────────────────────────────────────────────────────────

class PolicyVersion(Document):
    """Tracks chronological and contractual policy version releases."""

    version_id: Indexed(str, unique=True) = Field(alias="versionId")
    product_id: Indexed(str) = Field(alias="productId")
    product_name: Indexed(str) = Field(alias="productName")
    version_number: Indexed(str) = Field(alias="versionNumber")  # e.g. "2026.01", "3.0"
    effective_from: datetime = Field(alias="effectiveFrom")
    effective_to: Optional[datetime] = Field(default=None, alias="effectiveTo")
    is_current: bool = Field(default=True, alias="isCurrent")
    change_summary: Optional[str] = Field(default="", alias="changeSummary")
    source_documents: Optional[List[Dict[str, Any]]] = Field(default_factory=list, alias="sourceDocuments")

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    class Settings:
        name = "kb_policy_versions"
        use_state_management = True
        indexes = [
            "version_id",
            "product_id",
            "version_number",
            "is_current",
            "effective_from",
            "effective_to",
            [("product_name", 1), ("version_number", 1)],
            [("effective_from", 1), ("effective_to", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 5. Policy Clause Model
# ──────────────────────────────────────────────────────────────────────────────

class PolicyClause(Document):
    """Textual clause extracted from policy wording or product documentation."""

    clause_id: Indexed(str, unique=True) = Field(alias="clauseId")
    product_id: Indexed(str) = Field(alias="productId")
    product_name: Indexed(str) = Field(alias="productName")
    policy_version: Indexed(str) = Field(alias="policyVersion")
    clause_number: Optional[str] = Field(default="", alias="clauseNumber")
    title: Indexed(str)
    category: Indexed(str)  # WAITING_PERIOD, EXCLUSION, BENEFIT, PROCEDURE, GENERAL_CONDITION
    clause_text: str = Field(alias="clauseText")

    # Source Reference
    source_document: str = Field(alias="sourceDocument")
    source_page: Optional[int] = Field(default=None, alias="sourcePage")
    source_section: Optional[str] = Field(default="", alias="sourceSection")
    authority_level: str = Field(default=AuthorityLevel.REFERENCE_BENCHMARK.value, alias="authorityLevel")

    # RAG Compatibility
    tags: Optional[List[str]] = Field(default_factory=list)
    embedding: Optional[List[float]] = None

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    class Settings:
        name = "kb_policy_clauses"
        use_state_management = True
        indexes = [
            "clause_id",
            "product_name",
            "policy_version",
            "category",
            "title",
            [("product_name", 1), ("category", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 6. Policy Evidence Model
# ──────────────────────────────────────────────────────────────────────────────

class PolicyEvidence(Document):
    """Source evidence snippet linking rules to original PDF pages and text chunks."""

    evidence_id: Indexed(str, unique=True) = Field(alias="evidenceId")
    document_name: Indexed(str) = Field(alias="documentName")
    document_type: str = Field(default="reference_document", alias="documentType")
    page_number: Optional[int] = Field(default=None, alias="pageNumber")
    section_header: Optional[str] = Field(default="", alias="sectionHeader")
    text_snippet: str = Field(alias="textSnippet")
    authority_level: str = Field(default=AuthorityLevel.REFERENCE_BENCHMARK.value, alias="authorityLevel")
    tags: Optional[List[str]] = Field(default_factory=list)
    extracted_entities: Optional[Dict[str, Any]] = Field(default_factory=dict, alias="extractedEntities")
    embedding: Optional[List[float]] = None

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    class Settings:
        name = "kb_policy_evidences"
        use_state_management = True
        indexes = [
            "evidence_id",
            "document_name",
            "page_number",
            "authority_level",
            [("document_name", 1), ("page_number", 1)],
        ]

    class Config:
        populate_by_name = True


# ──────────────────────────────────────────────────────────────────────────────
# 7. Coverage Rule Model (Unified Data-Driven Rule Engine)
# ──────────────────────────────────────────────────────────────────────────────

class CoverageRule(Document):
    """
    Data-driven policy rule supporting waiting periods, permanent exclusions,
    benefits, limits, sub-limits, co-pays, networks, and hospitalization requirements.
    Every rule preserves source document and page citations.
    """

    # Rule Identification
    rule_id: Indexed(str, unique=True) = Field(alias="ruleId")
    insurer_name: Indexed(str) = Field(alias="insurerName")  # e.g. "Niva Bupa Health Insurance Co Ltd" or "All"
    product_name: Indexed(str) = Field(alias="productName")  # e.g. "ReAssure 3.0" or "Standard Benchmark"
    variant_name: Indexed(str) = Field(default="All", alias="variantName")  # "Classic", "Select", "Elite", "Black", or "All"
    policy_version: Indexed(str) = Field(default="2026.01", alias="policyVersion")

    # Rule Classification & Decision
    rule_type: Indexed(str) = Field(alias="ruleType")  # RuleType enum value
    condition: Indexed(str)  # Medical condition or benefit category (e.g. "Cataract", "Cosmetic Surgery")
    benefit_name: Optional[str] = Field(default="", alias="benefitName")
    decision: Optional[str] = Field(default=DecisionOutcome.COVERED.value)  # DecisionOutcome enum value
    rule_value: Dict[str, Any] = Field(default_factory=dict, alias="ruleValue")
    # e.g. {"waiting_period_months": 24}, {"limit_inr": 500000}, {"copay_pct": 0}, {"min_hospitalization_hours": 2}

    # Temporal Effectiveness
    effective_from: Optional[datetime] = Field(default=None, alias="effectiveFrom")
    effective_to: Optional[datetime] = Field(default=None, alias="effectiveTo")
    is_active: bool = Field(default=True, alias="isActive")

    # Authority and Precedence
    authority_level: str = Field(default=AuthorityLevel.REFERENCE_BENCHMARK.value, alias="authorityLevel")
    # "REFERENCE_BENCHMARK" (Product/benchmark reference) vs "CONTRACTUAL_POLICY_WORDING" (Uploaded policy takes precedence)

    # Source Evidence Citation
    source_document: str = Field(alias="sourceDocument")
    source_page: Optional[int] = Field(default=None, alias="sourcePage")
    source_section: Optional[str] = Field(default="", alias="sourceSection")
    source_text: Optional[str] = Field(default="", alias="sourceText")
    evidence_id: Optional[str] = Field(default="", alias="evidenceId")

    # Clinical / Semantic Matching Metadata (for deterministic & fuzzy rule evaluation)
    keywords: Optional[List[str]] = Field(default_factory=list)
    advisory_notes: Optional[str] = Field(default="", alias="advisoryNotes")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")
    updated_by: Optional[str] = Field(default="system", alias="updatedBy")
    source_version: Optional[str] = Field(default="1.0.0", alias="sourceVersion")

    @field_validator("condition", "rule_type", "source_document", mode="before")
    @classmethod
    def validate_non_empty_strings(cls, v: Any) -> str:
        if not v or not str(v).strip():
            raise ValueError("Field cannot be empty")
        return str(v).strip()

    class Settings:
        name = "kb_coverage_rules"
        use_state_management = True
        indexes = [
            "rule_id",
            "insurer_name",
            "product_name",
            "variant_name",
            "policy_version",
            "rule_type",
            "condition",
            "effective_from",
            "effective_to",
            "is_active",
            # Fast compound indexes for applicable rules retrieval
            [("insurer_name", 1), ("product_name", 1), ("variant_name", 1), ("is_active", 1)],
            [("rule_type", 1), ("condition", 1), ("is_active", 1)],
            [("policy_version", 1), ("effective_from", 1), ("effective_to", 1)],
            [("insurer_name", 1), ("rule_type", 1), ("is_active", 1)],
        ]

    class Config:
        populate_by_name = True
