"""
Knowledge Base API Endpoints.
Provides RESTful management of insurance companies, products, variants,
coverage rules, evidence citations, and claim evaluation against the Knowledge Base.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, status

from app.services.knowledge_base.policy_knowledge_service import PolicyKnowledgeService
from app.schemas.knowledge_base import (
    InsuranceCompanyCreate,
    InsuranceCompanyUpdate,
    InsuranceCompanyResponse,
    InsuranceProductCreate,
    ProductVariantCreate,
    CoverageRuleCreate,
    CoverageRuleUpdate,
    CoverageRuleResponse,
    ApplicableRulesQuery,
    ClaimEvaluationRequest,
    PolicyEvidenceResponse,
)

router = APIRouter(prefix="/knowledge-base", tags=["Policy Knowledge Base"])


# ──────────────────────────────────────────────────────────────────────────────
# Companies Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/companies", summary="List insurance companies")
async def list_companies(is_active: Optional[bool] = True):
    companies = await PolicyKnowledgeService.get_all_companies(is_active=is_active)
    return {"success": True, "count": len(companies), "data": companies}


@router.get("/companies/{company_id}", summary="Get insurance company by ID")
async def get_company(company_id: str):
    company = await PolicyKnowledgeService.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail=f"Company with ID '{company_id}' not found")
    return {"success": True, "data": company}


@router.post("/companies", status_code=status.HTTP_201_CREATED, summary="Register a new insurance company")
async def create_company(payload: InsuranceCompanyCreate):
    try:
        company = await PolicyKnowledgeService.create_company(payload.model_dump(by_alias=True, exclude_unset=True))
        return {"success": True, "data": company}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create company: {str(e)}")


@router.put("/companies/{company_id}", summary="Update insurance company")
async def update_company(company_id: str, payload: InsuranceCompanyUpdate):
    updated = await PolicyKnowledgeService.update_company(company_id, payload.model_dump(by_alias=True, exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail=f"Company with ID '{company_id}' not found")
    return {"success": True, "data": updated}


# ──────────────────────────────────────────────────────────────────────────────
# Products & Variants Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/products", summary="List insurance products")
async def list_products(company: Optional[str] = None):
    products = await PolicyKnowledgeService.get_all_products(company_name=company)
    return {"success": True, "count": len(products), "data": products}


@router.get("/variants", summary="List product variants")
async def list_variants(product: Optional[str] = None):
    variants = await PolicyKnowledgeService.get_variants(product_name=product)
    return {"success": True, "count": len(variants), "data": variants}


# ──────────────────────────────────────────────────────────────────────────────
# Coverage Rules Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/rules", summary="Filter and list coverage rules")
async def list_rules(
    insurer: Optional[str] = None,
    product: Optional[str] = None,
    variant: Optional[str] = None,
    rule_type: Optional[str] = None,
    condition: Optional[str] = None,
    include_inactive: bool = False,
):
    rules = await PolicyKnowledgeService.retrieve_applicable_rules(
        insurer_name=insurer,
        product_name=product,
        variant_name=variant,
        rule_type=rule_type,
        condition=condition,
        include_inactive=include_inactive,
    )
    return {"success": True, "count": len(rules), "data": rules}


@router.get("/rules/{rule_id}", summary="Get coverage rule by ID")
async def get_rule(rule_id: str):
    rule = await PolicyKnowledgeService.get_rule_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"success": True, "data": rule}


@router.post("/rules", status_code=status.HTTP_201_CREATED, summary="Create a new coverage rule")
async def create_rule(payload: CoverageRuleCreate):
    try:
        rule = await PolicyKnowledgeService.create_rule(payload.model_dump(by_alias=True, exclude_unset=True))
        return {"success": True, "data": rule}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create rule: {str(e)}")


@router.put("/rules/{rule_id}", summary="Update an existing coverage rule")
async def update_rule(rule_id: str, payload: CoverageRuleUpdate):
    updated = await PolicyKnowledgeService.update_rule(rule_id, payload.model_dump(by_alias=True, exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"success": True, "data": updated}


@router.delete("/rules/{rule_id}", summary="Deactivate a coverage rule")
async def deactivate_rule(rule_id: str):
    deactivated = await PolicyKnowledgeService.deactivate_rule(rule_id)
    if not deactivated:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"success": True, "message": f"Rule '{rule_id}' has been deactivated", "data": deactivated}


@router.post("/rules/applicable", summary="Query rules applicable to specific claim criteria")
async def query_applicable_rules(query: ApplicableRulesQuery):
    rules = await PolicyKnowledgeService.retrieve_applicable_rules(
        insurer_name=query.insurerName,
        product_name=query.productName,
        variant_name=query.variantName,
        rule_type=query.ruleType,
        condition=query.condition,
        as_of_date=query.asOfDate,
        include_inactive=query.includeInactive or False,
    )
    return {"success": True, "count": len(rules), "data": rules}


# ──────────────────────────────────────────────────────────────────────────────
# Claim Evaluation & Evidence Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/evaluate", summary="Screen medical claim details against Knowledge Base rules")
async def evaluate_claim(payload: ClaimEvaluationRequest):
    policy_data = {
        "insuranceCompany": payload.insurerName,
        "policyName": payload.productName,
        "policyType": payload.variantName,
    }
    prescription_data = {
        "diagnosis": payload.diagnosis,
        "symptoms": payload.symptoms or [],
        "procedures": payload.procedures or [],
        "medicines": payload.medicines or [],
    }
    result = await PolicyKnowledgeService.evaluate_claim_against_knowledge_base(
        policy_data=policy_data,
        prescription_data=prescription_data,
        as_of_date=payload.consultationDate,
    )
    return {"success": True, "data": result}


@router.get("/evidence/{evidence_id}", summary="Retrieve evidence snippet by ID")
async def get_evidence(evidence_id: str):
    evidence = await PolicyKnowledgeService.get_evidence(evidence_id)
    if not evidence:
        raise HTTPException(status_code=404, detail=f"Evidence snippet '{evidence_id}' not found")
    return {"success": True, "data": evidence}


@router.get("/evidence", summary="Search policy evidence snippets")
async def search_evidence(query: Optional[str] = Query(default="", description="Keyword search")):
    results = await PolicyKnowledgeService.search_evidence(query)
    return {"success": True, "count": len(results), "data": results}


@router.post("/seed", summary="Initialize or refresh Knowledge Base seed data")
async def seed_knowledge_base(force: bool = False):
    counts = await PolicyKnowledgeService.seed_knowledge_base(force_refresh=force)
    return {"success": True, "message": "Knowledge base seeding complete", "recordsSeeded": counts}
