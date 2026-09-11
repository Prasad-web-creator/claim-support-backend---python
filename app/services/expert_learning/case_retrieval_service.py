"""
Similar Case Retrieval Service.
Retrieves curated, de-identified, expert-approved historical claim cases
for a new claim using policy filtering and semantic vector similarity.
Strictly excludes unapproved, pending, or rejected cases.
"""

from typing import Dict, Any, List, Optional

from app.core.logging import logger
from app.models.expert_review import ApprovedExpertCase, ExpertReviewStatus
from app.services.expert_learning.embedding_provider import (
    LocalSemanticEmbeddingProvider,
    BaseCaseEmbeddingProvider,
    build_case_semantic_text,
)
from app.services.rule_engine.condition_normalizer import ConditionNormalizer
from app.services.rule_engine.date_utils import (
    parse_flexible_date,
    completed_calendar_months,
)


class SimilarCaseRetrievalService:
    """Policy-aware semantic retrieval of approved expert cases."""

    embedding_provider = LocalSemanticEmbeddingProvider()

    @classmethod
    async def retrieve_similar_cases(
        cls,
        claim_data: Optional[Dict[str, Any]] = None,
        policy_data: Optional[Dict[str, Any]] = None,
        prescription_json: Optional[Dict[str, Any]] = None,
        policy_json: Optional[Dict[str, Any]] = None,
        top_k: int = 3,
        limit: Optional[int] = None,
        similarity_threshold: float = 0.25,
        min_similarity: Optional[float] = None,
        target_version: Optional[str] = None,
        policy_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves top_k most similar approved expert cases matching policy context and medical presentation.
        Strictly guarantees:
        - Only cases where status == 'APPROVED' and isActive == True are returned.
        - Cross-insurer leakage is prevented when specific insurer is specified.
        """
        policy_data = policy_data or policy_json or {}
        claim_data = claim_data or prescription_json or {}
        top_k = limit if limit is not None else top_k
        similarity_threshold = min_similarity if min_similarity is not None else similarity_threshold
        req_version = target_version or policy_version or policy_data.get("policyVersion")

        # 1. Extract and normalize query parameters
        diag_raw = claim_data.get("diagnosis") or (claim_data.get("symptoms") or ["General"])[0]
        proc_raw = (claim_data.get("procedures") or ["Treatment"])[0]

        norm_diag = ConditionNormalizer.normalize_text(str(diag_raw), source="diagnosis").normalized_value
        norm_proc = ConditionNormalizer.normalize_text(str(proc_raw), source="procedure").normalized_value

        req_insurer = (policy_data.get("insuranceCompany") or policy_data.get("insurer") or "").strip().lower()
        req_product = (policy_data.get("policyName") or policy_data.get("product") or "").strip().lower()
        req_variant = (policy_data.get("variant") or policy_data.get("policyType") or "").strip().lower()

        # Compute policy age for query
        start_d = parse_flexible_date(policy_data.get("policyStartDate") or policy_data.get("effectiveFrom"))
        treat_d = parse_flexible_date(claim_data.get("visitDate") or claim_data.get("consultationDate"))
        query_age_months = completed_calendar_months(start_d, treat_d) if (start_d and treat_d) else None

        # 2. Database Query: Only APPROVED and active cases
        db_query: Dict[str, Any] = {
            "status": ExpertReviewStatus.APPROVED.value,
            "isActive": True,
        }

        if req_version:
            db_query["$or"] = [
                {"policyVersion": str(req_version)},
                {"policy_version": str(req_version)}
            ]

        candidates: List[ApprovedExpertCase] = await ApprovedExpertCase.find(db_query).to_list()
        if not candidates:
            logger.debug("[SimilarCaseRetrieval] No approved candidate expert cases found.")
            return []

        # 3. In-Memory Filter Verification (Insurer, Product, Variant Isolation)
        eligible_candidates: List[ApprovedExpertCase] = []
        for c in candidates:
            c_ins = c.insurer.strip().lower()
            if c_ins not in ("all", "universal", ""):
                if req_insurer and req_insurer not in ("all", "---", "none", ""):
                    if c_ins not in req_insurer and req_insurer not in c_ins:
                        continue

            c_prod = c.product.strip().lower()
            if c_prod not in ("all", "standard benchmark", "universal", ""):
                if req_product and req_product not in ("all", "---", "none", ""):
                    if c_prod not in req_product and req_product not in c_prod:
                        continue

            c_var = c.variant.strip().lower()
            if c_var not in ("all", "universal", ""):
                if req_variant and req_variant not in ("all", "---", "none", ""):
                    if c_var != req_variant:
                        continue

            eligible_candidates.append(c)

        if not eligible_candidates:
            return []

        # 4. Compute Query Vector
        query_semantic_text = build_case_semantic_text({
            "diagnosis": diag_raw,
            "normalizedDiagnosis": norm_diag,
            "treatment": proc_raw,
            "normalizedTreatment": norm_proc,
            "policyAgeMonths": query_age_months,
            "applicableRuleTypes": claim_data.get("applicableRuleTypes") or [],
        })
        query_vector = await cls.embedding_provider.generate_embedding(query_semantic_text)

        # 5. Semantic Vector Scoring
        scored: List[tuple[float, ApprovedExpertCase]] = []
        for c in eligible_candidates:
            vec = c.embedding
            if not vec:
                vec = await cls.embedding_provider.generate_embedding(
                    build_case_semantic_text(c.model_dump(by_alias=True))
                )

            sim = BaseCaseEmbeddingProvider.cosine_similarity(query_vector, vec)

            # Boost exact diagnosis match
            if c.normalized_diagnosis == norm_diag:
                sim += 0.35
            # Boost exact insurer / product match
            if c.insurer.lower() == req_insurer:
                sim += 0.15

            if sim >= similarity_threshold:
                scored.append((sim, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_cases = scored[:top_k]

        results: List[Dict[str, Any]] = []
        for score, case in top_cases:
            results.append({
                "approvedCaseId": case.approved_case_id,
                "caseId": case.case_id,
                "insurer": case.insurer,
                "product": case.product,
                "variant": case.variant,
                "policyVersion": case.policy_version,
                "diagnosis": case.diagnosis,
                "normalizedDiagnosis": case.normalized_diagnosis,
                "treatment": case.treatment,
                "policyAgeMonths": case.policy_age_months,
                "decision": case.decision,
                "reasonCode": case.reason_code,
                "expertExplanation": case.expert_explanation,
                "similarityScore": round(min(1.0, score), 3),
                "evidence": [e.model_dump(by_alias=True) for e in case.evidence],
            })

        logger.info(f"[SimilarCaseRetrieval] Retrieved {len(results)} approved expert cases for '{norm_diag}'.")
        return results

    @classmethod
    def format_expert_cases_for_prompt(cls, expert_cases: List[Dict[str, Any]]) -> str:
        """
        Formats retrieved cases into strict prompt context with advisory warnings.
        """
        if not expert_cases:
            return "• None available for this specific query."

        lines: List[str] = [
            "(The following approved cases are HISTORICAL ADVISORY EXAMPLES from verified medical experts.",
            " MANDATORY HIERARCHY DIRECTIVES:",
            " 1. Use current policy evidence first (Contractual wording takes absolute priority).",
            " 2. Use deterministic rule results second.",
            " 3. Use approved expert cases ONLY as supporting examples.",
            " 4. NEVER treat a historical claim as proof that another claim is covered.):\n"
        ]

        for i, c in enumerate(expert_cases, 1):
            months_str = f"{c.get('policyAgeMonths')} months" if c.get("policyAgeMonths") is not None else "Active"
            lines.append(
                f"• Approved Case #{i} [{c.get('approvedCaseId')}]:\n"
                f"  Insurer: {c.get('insurer')} | Product: {c.get('product')} ({c.get('variant')}) | Policy Version: {c.get('policyVersion')}\n"
                f"  Diagnosis: {c.get('diagnosis')} ({c.get('normalizedDiagnosis')}) | Treatment: {c.get('treatment')}\n"
                f"  Policy Age at Claim: {months_str}\n"
                f"  Expert Verdict: {c.get('decision')} (Reason: {c.get('reasonCode')})\n"
                f"  Expert Rationale: {c.get('expertExplanation')}\n"
            )

        return "\n".join(lines)
