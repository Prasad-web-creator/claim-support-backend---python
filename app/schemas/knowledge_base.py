"""
Pydantic schemas and validation for Knowledge Base entities and rules.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, model_validator, field_validator


class InsuranceCompanyBase(BaseModel):
    name: str = Field(..., min_length=2)
    code: Optional[str] = ""
    category: Optional[str] = "Private General Insurer"
    marketRank: Optional[int] = None
    marketSharePct: Optional[float] = None
    solvencyRatio: Optional[float] = None
    solvencyStatus: Optional[str] = "Healthy"
    isActive: Optional[bool] = True
    metadata: Optional[Dict[str, Any]] = None


class InsuranceCompanyCreate(InsuranceCompanyBase):
    companyId: Optional[str] = None


class InsuranceCompanyUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    marketRank: Optional[int] = None
    marketSharePct: Optional[float] = None
    solvencyRatio: Optional[float] = None
    solvencyStatus: Optional[str] = None
    isActive: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class InsuranceCompanyResponse(InsuranceCompanyBase):
    companyId: str
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────────
# Product & Variant Schemas
# ──────────────────────────────────────────────────────────────────────────────

class InsuranceProductCreate(BaseModel):
    productId: Optional[str] = None
    companyName: str = Field(..., min_length=2)
    name: str = Field(..., min_length=2)
    productType: Optional[str] = "Comprehensive Health Insurance"
    description: Optional[str] = ""
    currentVersion: Optional[str] = "1.0"
    isActive: Optional[bool] = True
    metadata: Optional[Dict[str, Any]] = None


class ProductVariantCreate(BaseModel):
    variantId: Optional[str] = None
    productId: str
    productName: str
    companyName: str
    name: str = Field(..., min_length=1)
    description: Optional[str] = ""
    roomCategory: Optional[str] = ""
    roadAmbulanceLimit: Optional[str] = ""
    airAmbulanceLimit: Optional[str] = ""
    modernTreatmentLimit: Optional[str] = ""
    lockTheClock: Optional[bool] = False
    personalAccidentOptional: Optional[str] = ""
    hospitalDailyCashOptional: Optional[str] = ""
    borderlessOptional: Optional[str] = ""
    isActive: Optional[bool] = True
    features: Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────────────────────────────────────
# Version & Clause Schemas
# ──────────────────────────────────────────────────────────────────────────────

class PolicyVersionCreate(BaseModel):
    versionId: Optional[str] = None
    productId: str
    productName: str
    versionNumber: str
    effectiveFrom: datetime
    effectiveTo: Optional[datetime] = None
    isCurrent: Optional[bool] = True
    changeSummary: Optional[str] = ""
    sourceDocuments: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.effectiveTo and self.effectiveFrom > self.effectiveTo:
            raise ValueError("effectiveFrom cannot be later than effectiveTo")
        return self


class PolicyClauseCreate(BaseModel):
    clauseId: Optional[str] = None
    productId: str
    productName: str
    policyVersion: str
    clauseNumber: Optional[str] = ""
    title: str = Field(..., min_length=2)
    category: str = Field(..., min_length=2)
    clauseText: str = Field(..., min_length=5)
    sourceDocument: str = Field(..., min_length=2)
    sourcePage: Optional[int] = None
    sourceSection: Optional[str] = ""
    authorityLevel: Optional[str] = "REFERENCE_BENCHMARK"
    tags: Optional[List[str]] = None


# ──────────────────────────────────────────────────────────────────────────────
# Policy Evidence Schema
# ──────────────────────────────────────────────────────────────────────────────

class PolicyEvidenceCreate(BaseModel):
    evidenceId: Optional[str] = None
    documentName: str = Field(..., min_length=2)
    documentType: Optional[str] = "reference_document"
    pageNumber: Optional[int] = None
    sectionHeader: Optional[str] = ""
    textSnippet: str = Field(..., min_length=5)
    authorityLevel: Optional[str] = "REFERENCE_BENCHMARK"
    tags: Optional[List[str]] = None
    extractedEntities: Optional[Dict[str, Any]] = None


class PolicyEvidenceResponse(BaseModel):
    evidenceId: str
    documentName: str
    documentType: str
    pageNumber: Optional[int]
    sectionHeader: Optional[str]
    textSnippet: str
    authorityLevel: str
    tags: List[str] = []
    extractedEntities: Dict[str, Any] = {}
    createdAt: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────────
# Coverage Rule Schemas (Creation, Update, Query, Response)
# ──────────────────────────────────────────────────────────────────────────────

class CoverageRuleCreate(BaseModel):
    ruleId: Optional[str] = None
    insurerName: str = Field(..., min_length=1)
    productName: str = Field(..., min_length=1)
    variantName: Optional[str] = "All"
    policyVersion: Optional[str] = "2026.01"

    ruleType: str = Field(..., description="WAITING_PERIOD, PERMANENT_EXCLUSION, BENEFIT, etc.")
    condition: str = Field(..., min_length=1, description="Condition or benefit title")
    benefitName: Optional[str] = ""
    decision: Optional[str] = "COVERED"
    ruleValue: Dict[str, Any] = Field(default_factory=dict)

    effectiveFrom: Optional[datetime] = None
    effectiveTo: Optional[datetime] = None
    isActive: Optional[bool] = True
    authorityLevel: Optional[str] = "REFERENCE_BENCHMARK"

    sourceDocument: str = Field(..., min_length=1)
    sourcePage: Optional[int] = None
    sourceSection: Optional[str] = ""
    sourceText: Optional[str] = ""
    evidenceId: Optional[str] = ""

    keywords: Optional[List[str]] = Field(default_factory=list)
    advisoryNotes: Optional[str] = ""
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_dates_and_rule_type(self):
        if self.effectiveFrom and self.effectiveTo and self.effectiveFrom > self.effectiveTo:
            raise ValueError("effectiveFrom cannot be later than effectiveTo")
        valid_types = {
            "WAITING_PERIOD", "PERMANENT_EXCLUSION", "BENEFIT", "OPTIONAL_BENEFIT",
            "LIMIT", "SUB_LIMIT", "CO_PAY", "DEDUCTIBLE", "NETWORK_REQUIREMENT",
            "GEOGRAPHICAL_RESTRICTION", "HOSPITALIZATION_REQUIREMENT"
        }
        if self.ruleType not in valid_types:
            raise ValueError(f"Invalid ruleType '{self.ruleType}'. Allowed types: {sorted(list(valid_types))}")
        return self


class CoverageRuleUpdate(BaseModel):
    variantName: Optional[str] = None
    decision: Optional[str] = None
    ruleValue: Optional[Dict[str, Any]] = None
    effectiveFrom: Optional[datetime] = None
    effectiveTo: Optional[datetime] = None
    isActive: Optional[bool] = None
    advisoryNotes: Optional[str] = None
    keywords: Optional[List[str]] = None
    sourceDocument: Optional[str] = None
    sourcePage: Optional[int] = None
    sourceSection: Optional[str] = None
    sourceText: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CoverageRuleResponse(BaseModel):
    ruleId: str
    insurerName: str
    productName: str
    variantName: str
    policyVersion: str
    ruleType: str
    condition: str
    benefitName: Optional[str] = ""
    decision: Optional[str] = ""
    ruleValue: Dict[str, Any] = {}
    effectiveFrom: Optional[datetime] = None
    effectiveTo: Optional[datetime] = None
    isActive: bool
    authorityLevel: str
    sourceDocument: str
    sourcePage: Optional[int] = None
    sourceSection: Optional[str] = ""
    sourceText: Optional[str] = ""
    evidenceId: Optional[str] = ""
    keywords: List[str] = []
    advisoryNotes: Optional[str] = ""
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────────
# Query & Evaluation Schemas
# ──────────────────────────────────────────────────────────────────────────────

class ApplicableRulesQuery(BaseModel):
    insurerName: Optional[str] = None
    productName: Optional[str] = None
    variantName: Optional[str] = None
    ruleType: Optional[str] = None
    condition: Optional[str] = None
    asOfDate: Optional[datetime] = None
    includeInactive: Optional[bool] = False


class ClaimEvaluationRequest(BaseModel):
    insurerName: Optional[str] = ""
    productName: Optional[str] = ""
    variantName: Optional[str] = ""
    diagnosis: Optional[str] = ""
    symptoms: Optional[List[str]] = None
    procedures: Optional[List[str]] = None
    medicines: Optional[List[Any]] = None
    consultationDate: Optional[datetime] = None
    isPreExisting: Optional[bool] = False
    policyActiveDays: Optional[int] = None
