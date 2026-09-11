"""
Policy Knowledge Base Service.
Provides high-performance data-driven rule queries, lifecycle management,
CRUD operations, source evidence retrieval, and claim evaluation against the Knowledge Base.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import re

from app.core.logging import logger
from app.models.knowledge_base import (
    InsuranceCompany,
    InsuranceProduct,
    ProductVariant,
    PolicyVersion,
    PolicyClause,
    CoverageRule,
    PolicyEvidence,
    AuthorityLevel,
)
from app.services.knowledge_base.seed_data import (
    SEED_COMPANIES,
    SEED_PRODUCTS,
    SEED_VARIANTS,
    SEED_VERSIONS,
    SEED_EVIDENCES,
    ALL_SEED_RULES,
)


class PolicyKnowledgeService:
    """Service layer encapsulating all operations on the Policy Knowledge Base."""

    # ──────────────────────────────────────────────────────────────────────────
    # Initialization & Seeding
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def seed_knowledge_base(cls, force_refresh: bool = False) -> Dict[str, int]:
        """
        Seeds initial reference standards, products, variants, 14 waiting periods,
        and 32 permanent exclusions into MongoDB if not already present.
        """
        logger.info("[KnowledgeBase] Checking Knowledge Base initialization status...")
        counts = {
            "companies": 0,
            "products": 0,
            "variants": 0,
            "versions": 0,
            "evidences": 0,
            "rules": 0,
        }

        # 1. Companies
        for c in SEED_COMPANIES:
            existing = await InsuranceCompany.find_one(InsuranceCompany.company_id == c["companyId"])
            if not existing or force_refresh:
                if existing:
                    await existing.delete()
                doc = InsuranceCompany(**c)
                await doc.insert()
                counts["companies"] += 1

        # 2. Products
        for p in SEED_PRODUCTS:
            existing = await InsuranceProduct.find_one(InsuranceProduct.product_id == p["productId"])
            if not existing or force_refresh:
                if existing:
                    await existing.delete()
                doc = InsuranceProduct(**p)
                await doc.insert()
                counts["products"] += 1

        # 3. Variants
        for v in SEED_VARIANTS:
            existing = await ProductVariant.find_one(ProductVariant.variant_id == v["variantId"])
            if not existing or force_refresh:
                if existing:
                    await existing.delete()
                doc = ProductVariant(**v)
                await doc.insert()
                counts["variants"] += 1

        # 4. Versions
        for ver in SEED_VERSIONS:
            existing = await PolicyVersion.find_one(PolicyVersion.version_id == ver["versionId"])
            if not existing or force_refresh:
                if existing:
                    await existing.delete()
                doc = PolicyVersion(**ver)
                await doc.insert()
                counts["versions"] += 1

        # 5. Evidences
        for ev in SEED_EVIDENCES:
            existing = await PolicyEvidence.find_one(PolicyEvidence.evidence_id == ev["evidenceId"])
            if not existing or force_refresh:
                if existing:
                    await existing.delete()
                doc = PolicyEvidence(**ev)
                await doc.insert()
                counts["evidences"] += 1

        # 6. Coverage Rules
        for r in ALL_SEED_RULES:
            existing = await CoverageRule.find_one(CoverageRule.rule_id == r["ruleId"])
            if not existing or force_refresh:
                if existing:
                    await existing.delete()
                doc = CoverageRule(**r)
                await doc.insert()
                counts["rules"] += 1

        logger.info(f"[KnowledgeBase] Seed completed: {counts}")
        return counts

    # ──────────────────────────────────────────────────────────────────────────
    # Company Operations
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def get_all_companies(cls, is_active: Optional[bool] = True) -> List[InsuranceCompany]:
        query = {}
        if is_active is not None:
            query["isActive"] = is_active
        return await InsuranceCompany.find(query).sort("marketRank").to_list()

    @classmethod
    async def get_company_by_id(cls, company_id: str) -> Optional[InsuranceCompany]:
        return await InsuranceCompany.find_one(InsuranceCompany.company_id == company_id)

    @classmethod
    async def get_company_by_name(cls, name: str) -> Optional[InsuranceCompany]:
        if not name:
            return None
        clean_name = name.strip().lower()
        companies = await InsuranceCompany.find_all().to_list()
        for c in companies:
            if c.name.lower() == clean_name or clean_name in c.name.lower():
                return c
        return None

    @classmethod
    async def create_company(cls, data: Dict[str, Any], user_id: str = "system") -> InsuranceCompany:
        if not data.get("companyId"):
            code = data.get("code") or data["name"][:6].upper().replace(" ", "")
            data["companyId"] = f"INS-{code}"
        data["createdBy"] = user_id
        data["updatedBy"] = user_id
        company = InsuranceCompany(**data)
        return await company.insert()

    @classmethod
    async def update_company(cls, company_id: str, updates: Dict[str, Any], user_id: str = "system") -> Optional[InsuranceCompany]:
        company = await cls.get_company_by_id(company_id)
        if not company:
            return None
        for k, v in updates.items():
            if v is not None and hasattr(company, k):
                setattr(company, k, v)
        company.updated_at = datetime.now(timezone.utc)
        company.updated_by = user_id
        await company.save()
        return company

    # ──────────────────────────────────────────────────────────────────────────
    # Products & Variants Operations
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def get_all_products(cls, company_name: Optional[str] = None) -> List[InsuranceProduct]:
        query = {"isActive": True}
        if company_name:
            query["companyName"] = {"$regex": re.escape(company_name), "$options": "i"}
        return await InsuranceProduct.find(query).to_list()

    @classmethod
    async def get_variants(cls, product_name: Optional[str] = None) -> List[ProductVariant]:
        query = {"isActive": True}
        if product_name:
            query["productName"] = {"$regex": re.escape(product_name), "$options": "i"}
        return await ProductVariant.find(query).to_list()

    @classmethod
    async def get_variant_by_name(cls, product_name: str, variant_name: str) -> Optional[ProductVariant]:
        return await ProductVariant.find_one(
            ProductVariant.name == variant_name,
            ProductVariant.product_name == product_name,
            ProductVariant.is_active == True,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Evidence Operations
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def get_evidence(cls, evidence_id: str) -> Optional[PolicyEvidence]:
        return await PolicyEvidence.find_one(PolicyEvidence.evidence_id == evidence_id)

    @classmethod
    async def search_evidence(cls, query: str, limit: int = 10) -> List[PolicyEvidence]:
        if not query:
            return await PolicyEvidence.find_all().limit(limit).to_list()
        regex_query = {"$regex": re.escape(query), "$options": "i"}
        return await PolicyEvidence.find({
            "$or": [
                {"documentName": regex_query},
                {"sectionHeader": regex_query},
                {"textSnippet": regex_query},
                {"tags": regex_query},
            ]
        }).limit(limit).to_list()

    # ──────────────────────────────────────────────────────────────────────────
    # Coverage Rule Lifecycle & CRUD Operations
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def create_rule(cls, rule_data: Dict[str, Any], user_id: str = "system") -> CoverageRule:
        """Create a new validated data-driven coverage rule."""
        if not rule_data.get("ruleId"):
            prefix = rule_data.get("ruleType", "RULE")[:4]
            cond_slug = re.sub(r"[^A-Za-z0-9]", "-", rule_data.get("condition", "GEN")).upper()[:12]
            ts = int(datetime.now(timezone.utc).timestamp())
            rule_data["ruleId"] = f"{prefix}-{cond_slug}-{ts}"

        rule_data["createdBy"] = user_id
        rule_data["updatedBy"] = user_id
        rule = CoverageRule(**rule_data)
        return await rule.insert()

    @classmethod
    async def get_rule_by_id(cls, rule_id: str) -> Optional[CoverageRule]:
        return await CoverageRule.find_one(CoverageRule.rule_id == rule_id)

    @classmethod
    async def update_rule(cls, rule_id: str, updates: Dict[str, Any], user_id: str = "system") -> Optional[CoverageRule]:
        rule = await cls.get_rule_by_id(rule_id)
        if not rule:
            return None
        for k, v in updates.items():
            if v is not None and hasattr(rule, k):
                setattr(rule, k, v)
        rule.updated_at = datetime.now(timezone.utc)
        rule.updated_by = user_id
        await rule.save()
        return rule

    @classmethod
    async def deactivate_rule(cls, rule_id: str, user_id: str = "system") -> Optional[CoverageRule]:
        return await cls.update_rule(rule_id, {"is_active": False}, user_id=user_id)

    @classmethod
    async def deactivate_old_rules(
        cls,
        product_name: str,
        older_than_version: str,
        deactivate_before: Optional[datetime] = None,
        user_id: str = "system"
    ) -> int:
        """
        Deactivates outdated rules when a new policy version takes effect.
        """
        query: Dict[str, Any] = {
            "productName": product_name,
            "policyVersion": older_than_version,
            "isActive": True,
        }
        if deactivate_before:
            query["effectiveFrom"] = {"$lt": deactivate_before}

        rules_to_deactivate = await CoverageRule.find(query).to_list()
        count = 0
        now = datetime.now(timezone.utc)
        for r in rules_to_deactivate:
            r.is_active = False
            r.effective_to = deactivate_before or now
            r.updated_at = now
            r.updated_by = user_id
            await r.save()
            count += 1

        logger.info(f"[KnowledgeBase] Deactivated {count} old rules for {product_name} (version {older_than_version})")
        return count

    # ──────────────────────────────────────────────────────────────────────────
    # Applicable Rules Engine (Query & Temporal Matching)
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def retrieve_applicable_rules(
        cls,
        insurer_name: Optional[str] = None,
        product_name: Optional[str] = None,
        variant_name: Optional[str] = None,
        rule_type: Optional[str] = None,
        condition: Optional[str] = None,
        as_of_date: Optional[datetime] = None,
        include_inactive: bool = False,
    ) -> List[CoverageRule]:
        """
        High-performance retrieval of rules applicable to a specific policy/claim.
        Supports fallback across Insurer -> Product -> Variant hierarchy.
        """
        query: Dict[str, Any] = {}

        if not include_inactive:
            query["isActive"] = True

        # Rule Type filtering
        if rule_type:
            query["ruleType"] = rule_type

        # Insurer filtering (matches specified insurer OR universal 'All')
        if insurer_name and insurer_name.lower() not in ("all", "---", "none", ""):
            query["$or"] = [
                {"insurerName": {"$regex": re.escape(insurer_name), "$options": "i"}},
                {"insurerName": "All"},
            ]

        # Product filtering
        if product_name and product_name.lower() not in ("all", "---", "none", ""):
            p_criteria = [
                {"productName": {"$regex": re.escape(product_name), "$options": "i"}},
                {"productName": "Standard Benchmark"},
                {"productName": "All"},
            ]
            if "$or" in query:
                query["$and"] = [{"$or": query.pop("$or")}, {"$or": p_criteria}]
            else:
                query["$or"] = p_criteria

        # Variant filtering
        if variant_name and variant_name.lower() not in ("all", "---", "none", ""):
            v_criteria = [
                {"variantName": {"$regex": re.escape(variant_name), "$options": "i"}},
                {"variantName": "All"},
            ]
            if "$and" in query:
                query["$and"].append({"$or": v_criteria})
            elif "$or" in query:
                query["$and"] = [{"$or": query.pop("$or")}, {"$or": v_criteria}]
            else:
                query["$or"] = v_criteria

        # Condition filtering
        if condition:
            cond_query = {"$regex": re.escape(condition), "$options": "i"}
            query["$or"] = [
                {"condition": cond_query},
                {"keywords": cond_query},
                {"benefitName": cond_query},
            ]

        # Temporal validity filtering (effectiveFrom <= as_of_date <= effectiveTo)
        if as_of_date:
            date_filter = [
                {"$or": [{"effectiveFrom": None}, {"effectiveFrom": {"$lte": as_of_date}}]},
                {"$or": [{"effectiveTo": None}, {"effectiveTo": {"$gte": as_of_date}}]},
            ]
            if "$and" in query:
                query["$and"].extend(date_filter)
            else:
                query["$and"] = date_filter

        results = await CoverageRule.find(query).to_list()
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Claim Evaluation Engine (Data-Driven Screening)
    # ──────────────────────────────────────────────────────────────────────────
    @classmethod
    async def evaluate_claim_against_knowledge_base(
        cls,
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
        as_of_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Screens medical data against active Knowledge Base rules.
        Contractual authority note: uploaded policy text remains the highest authority;
        this provides reference benchmark detection and citations.
        """
        eval_date = as_of_date or datetime.now(timezone.utc)
        insurer = policy_data.get("insuranceCompany") or policy_data.get("company") or ""
        product = policy_data.get("policyName") or policy_data.get("product") or "ReAssure 3.0"
        variant = policy_data.get("policyType") or policy_data.get("variant") or "All"

        # Build medical search corpus
        tokens: List[str] = []
        if prescription_data.get("diagnosis"):
            tokens.append(str(prescription_data["diagnosis"]))
        if prescription_data.get("symptoms"):
            tokens.extend([str(s) for s in prescription_data["symptoms"]])
        if prescription_data.get("procedures"):
            tokens.extend([str(p) for p in prescription_data["procedures"]])
        if prescription_data.get("medicines"):
            for m in prescription_data["medicines"]:
                tokens.append(m.get("name") if isinstance(m, dict) else str(m))
        if prescription_data.get("manualText"):
            tokens.append(str(prescription_data["manualText"]))

        medical_corpus = " ".join(tokens).lower()

        # Fetch active waiting period rules
        wp_rules = await cls.retrieve_applicable_rules(
            insurer_name=insurer,
            product_name=product,
            variant_name=variant,
            rule_type="WAITING_PERIOD",
            as_of_date=eval_date,
        )

        detected_waiting_conditions = []
        for r in wp_rules:
            matched_kw = [kw for kw in (r.keywords or []) if kw in medical_corpus]
            if matched_kw or (r.condition.lower() in medical_corpus):
                detected_waiting_conditions.append({
                    "ruleId": r.rule_id,
                    "condition": r.condition,
                    "matchedKeyword": matched_kw[0] if matched_kw else r.condition,
                    "waitingPeriodMonths": r.rule_value.get("waitingPeriodMonths", 24),
                    "decision": r.decision,
                    "source": {
                        "document": r.source_document,
                        "page": r.source_page,
                        "section": r.source_section,
                        "text": r.source_text,
                    },
                    "advisory": r.advisory_notes,
                    "authorityLevel": r.authority_level,
                })

        # Fetch active permanent exclusion rules
        excl_rules = await cls.retrieve_applicable_rules(
            insurer_name=insurer,
            product_name=product,
            variant_name=variant,
            rule_type="PERMANENT_EXCLUSION",
            as_of_date=eval_date,
        )

        detected_permanent_exclusions = []
        for r in excl_rules:
            matched_kw = [kw for kw in (r.keywords or []) if kw in medical_corpus]
            if matched_kw or (r.condition.lower() in medical_corpus):
                detected_permanent_exclusions.append({
                    "ruleId": r.rule_id,
                    "exclusionNumber": r.rule_value.get("exclusionNumber"),
                    "title": r.condition,
                    "matchedKeyword": matched_kw[0] if matched_kw else r.condition,
                    "decision": r.decision,
                    "source": {
                        "document": r.source_document,
                        "page": r.source_page,
                        "section": r.source_section,
                        "text": r.source_text,
                    },
                    "warning": r.advisory_notes,
                    "authorityLevel": r.authority_level,
                })

        # Fetch variant-specific benefit & limit rules
        variant_rules = await cls.retrieve_applicable_rules(
            insurer_name=insurer,
            product_name=product,
            variant_name=variant,
            as_of_date=eval_date,
        )
        applicable_limits = []
        for r in variant_rules:
            if r.rule_type in ("LIMIT", "SUB_LIMIT", "HOSPITALIZATION_REQUIREMENT"):
                applicable_limits.append({
                    "ruleId": r.rule_id,
                    "benefitName": r.benefit_name or r.condition,
                    "ruleType": r.rule_type,
                    "ruleValue": r.rule_value,
                    "source": {
                        "document": r.source_document,
                        "page": r.source_page,
                        "section": r.source_section,
                    },
                    "advisory": r.advisory_notes,
                })

        return {
            "insurer": insurer or "Standard Health Benchmark",
            "product": product,
            "variant": variant,
            "evaluationDate": eval_date.isoformat(),
            "detectedWaitingConditions": detected_waiting_conditions,
            "detectedPermanentExclusions": detected_permanent_exclusions,
            "applicableLimits": applicable_limits,
            "hasWaitingPeriodFlag": len(detected_waiting_conditions) > 0,
            "hasPermanentExclusionFlag": len(detected_permanent_exclusions) > 0,
            "contractualAuthorityNote": "Reference rules derived from Knowledge Base standards. Specific policy wording remains highest contractual authority.",
        }
