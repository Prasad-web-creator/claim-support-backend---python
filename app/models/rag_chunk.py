"""
Policy RAG Chunk Document Model.
Stores semantically chunked, policy-aware clauses, sections, and metadata
for retrieval-augmented generation and auditable file search.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from enum import Enum

from beanie import Document, Indexed
from pydantic import Field, field_validator


class DocumentType(str, Enum):
    POLICY_WORDING = "policy wording"
    SCHEDULE = "schedule"
    ENDORSEMENT = "endorsement"
    TERMS_AND_CONDITIONS = "terms and conditions"
    BENEFITS_DOCUMENT = "benefits document"
    PRODUCT_BROCHURE = "product brochure"
    EXCLUSION_DOCUMENT = "exclusion document"
    WAITING_PERIOD_DOCUMENT = "waiting-period document"
    OPTIONAL_BENEFIT_DOCUMENT = "optional-benefit document"
    EXPERT_CASE_DOCUMENT = "expert case document"


class AuthorityPriority(str, Enum):
    CONTRACTUAL_POLICY_WORDING = "CONTRACTUAL_POLICY_WORDING"  # Priority 1 (Highest)
    STRUCTURED_POLICY_RULE = "STRUCTURED_POLICY_RULE"          # Priority 2
    PRODUCT_REFERENCE_DOCUMENT = "PRODUCT_REFERENCE_DOCUMENT"  # Priority 3
    EXPERT_CASE = "EXPERT_CASE"                                # Priority 4 (Informational only, never contractual)


class PolicyChunk(Document):
    """Semantically indexed clause chunk with policy isolation metadata."""

    chunk_id: Indexed(str, unique=True) = Field(alias="chunkId")
    policy_id: Indexed(str) = Field(alias="policyId")  # Specific user policy ID or "GLOBAL_REFERENCE"
    insurer: Indexed(str)
    product: Indexed(str)
    variant: Indexed(str) = Field(default="All")
    policy_version: Indexed(str) = Field(default="1.0", alias="policyVersion")

    # Document Reference
    document_id: Optional[str] = Field(default="", alias="documentId")
    document_type: str = Field(default=DocumentType.POLICY_WORDING.value, alias="documentType")
    page_number: Optional[int] = Field(default=None, alias="pageNumber")
    section: Optional[str] = Field(default="", alias="section")
    rule_type: Optional[str] = Field(default="GENERAL_CONDITIONS", alias="ruleType")

    # Temporal Effectiveness
    effective_from: Optional[datetime] = Field(default=None, alias="effectiveFrom")
    effective_to: Optional[datetime] = Field(default=None, alias="effectiveTo")

    # Content & Source
    source: str = Field(default="", description="Original file name or document identifier")
    language: str = Field(default="en")
    text: str = Field(..., min_length=5, description="Exact clause text chunk")
    authority_level: str = Field(
        default=AuthorityPriority.CONTRACTUAL_POLICY_WORDING.value,
        alias="authorityLevel"
    )

    # Retrieval and Search Enablers
    keywords: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # Audit fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="createdAt")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="updatedAt")
    created_by: Optional[str] = Field(default="system", alias="createdBy")

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, v: Any) -> str:
        if not v or not str(v).strip():
            raise ValueError("Chunk text cannot be empty")
        return str(v).strip()

    class Settings:
        name = "kb_policy_chunks"
        use_state_management = True
        indexes = [
            "chunk_id",
            "policy_id",
            "insurer",
            "product",
            "variant",
            "policy_version",
            "rule_type",
            "document_type",
            "authority_level",
            "effective_from",
            "effective_to",
            # Compound indexes for high-speed policy-isolated retrieval
            [("policy_id", 1), ("rule_type", 1)],
            [("insurer", 1), ("product", 1), ("variant", 1)],
            [("policy_version", 1), ("effective_from", 1), ("effective_to", 1)],
            [("policy_id", 1), ("authority_level", 1)],
        ]

    class Config:
        populate_by_name = True
