"""
Expert Case Management Service.
Handles claim case creation from analysis runs, expert review workflow
(Accept, Correct, Reject), immutable versioning, audit tracking,
de-identification, duplicate prevention, and trusted knowledge base storage.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.core.logging import logger
from app.models.expert_review import (
    ClaimCase,
    ClaimAnalysisVersion,
    ExpertReview,
    ExpertCorrection,
    ApprovedExpertCase,
    CaseEvidence,
    CaseEmbeddingMetadata,
    ExpertReviewStatus,
    ExpertAction,
)
from app.services.expert_learning.deidentifier import CaseDeidentifier
from app.services.expert_learning.embedding_provider import (
    LocalSemanticEmbeddingProvider,
    build_case_semantic_text,
)
from app.services.rule_engine.condition_normalizer import ConditionNormalizer
from app.services.rule_engine.date_utils import (
    parse_flexible_date,
    completed_calendar_months,
)


class ExpertCaseService:
    """Orchestrates expert review workflow and audit lifecycle for claim cases."""

    embedding_provider = LocalSemanticEmbeddingProvider()

    @classmethod
    async def create_case_from_report(
        cls,
        report_id: str,
        user_id: Optional[str] = None,
        policy_id: Optional[str] = None,
        prescription_id: Optional[str] = None,
    ) -> Optional[ClaimCase]:
        """
        Creates a ClaimCase directly from an AnalysisReport id.
        """
        from app.models.analysis_report import AnalysisReport
        from beanie import PydanticObjectId as ObjectId
        try:
            report = await AnalysisReport.get(ObjectId(report_id))
            if not report:
                logger.warning(f"[Case Review] AnalysisReport {report_id} not found.")
                return None
            return await cls.create_case_from_analysis(
                policy_json=report.policy_json or {},
                prescription_json=report.prescription_json or {},
                business_rule_results=report.business_rules or {},
                coverage_analysis=report.coverage_analysis or {},
                report_id=str(report.id),
                session_id=report.session_id,
                policy_id=str(report.policy_id or policy_id or ""),
            )
        except Exception as e:
            logger.error(f"[Case Review] Error creating case from report {report_id}: {e}", exc_info=True)
            return None

    @classmethod
    async def create_case_from_analysis(
        cls,
        policy_json: Optional[Dict[str, Any]] = None,
        prescription_json: Optional[Dict[str, Any]] = None,
        business_rule_results: Optional[Dict[str, Any]] = None,
        coverage_analysis: Optional[Dict[str, Any]] = None,
        report_id: Optional[str] = None,
        session_id: Optional[str] = None,
        policy_id: Optional[str] = None,
    ) -> ClaimCase:
        """
        Automatically captures a new ClaimCase upon completion of an analysis session.
        Initial state is strictly PENDING_EXPERT_REVIEW with immutable v1 audit snapshot.
        """
        policy_json = policy_json or {}
        prescription_json = prescription_json or {}
        business_rule_results = business_rule_results or {}
        coverage_analysis = coverage_analysis or {}

        # 1. Generate unique case ID
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_suffix = uuid.uuid4().hex[:6].upper()
        case_id = f"CASE-{ts_str}-{rand_suffix}"

        # 2. Extract policy & clinical details
        p_id = str(policy_id or policy_json.get("policyId") or policy_json.get("policyNumber") or "UNKNOWN_POL")
        insurer = policy_json.get("insuranceCompany") or policy_json.get("company") or "All"
        product = policy_json.get("policyName") or policy_json.get("product") or "Standard Benchmark"
        variant = policy_json.get("variant") or policy_json.get("policyType") or "All"
        policy_ver = str(policy_json.get("policyVersion") or "1.0")

        diag_raw = prescription_json.get("diagnosis") or (prescription_json.get("symptoms") or ["General Diagnosis"])[0]
        proc_raw = (prescription_json.get("procedures") or ["Standard Treatment"])[0]

        norm_diag = ConditionNormalizer.normalize_text(str(diag_raw), source="diagnosis").normalized_value
        norm_proc = ConditionNormalizer.normalize_text(str(proc_raw), source="procedure").normalized_value

        # 3. Calculate policy age in completed months
        start_d = parse_flexible_date(policy_json.get("policyStartDate") or policy_json.get("effectiveFrom"))
        treat_d = parse_flexible_date(prescription_json.get("visitDate") or prescription_json.get("consultationDate"))
        age_months = completed_calendar_months(start_d, treat_d) if (start_d and treat_d) else None

        # 4. Extract rule and AI outcomes
        det_res = business_rule_results.get("deterministicResult") or {}
        det_dec = det_res.get("status") or ("COVERED" if business_rule_results.get("overallEligible", True) else "NOT_COVERED")
        det_reason = det_res.get("reasonCode") or "POLICY_ACTIVE"

        analysis_inner = coverage_analysis.get("analysis") if isinstance(coverage_analysis.get("analysis"), dict) else coverage_analysis
        ai_dec = analysis_inner.get("overallStatus") or ("COVERED" if coverage_analysis.get("confidence", 0) > 60 else "NOT_COVERED")
        ai_exp = analysis_inner.get("summary") or coverage_analysis.get("reasoning") or ""

        # Extract rule IDs and evidence
        rule_ids = [r.get("ruleId") for r in det_res.get("ruleResults", []) if r.get("ruleId")]
        ev_items: List[CaseEvidence] = []
        for r in det_res.get("ruleResults", []):
            if r.get("evidence"):
                ev = r["evidence"]
                ev_items.append(CaseEvidence(
                    documentName=ev.get("sourceDocument") or "Policy_Document.pdf",
                    pageNumber=ev.get("page") or ev.get("pageNumber"),
                    section=ev.get("section") or "",
                    clauseId=ev.get("clauseId") or "",
                    textSnippet=ev.get("textSnippet") or "",
                    authorityLevel=ev.get("authorityLevel") or "CONTRACTUAL_POLICY_WORDING",
                ))

        claim_case = ClaimCase(
            caseId=case_id,
            analysisSessionId=session_id or "",
            reportId=report_id or "",
            policyId=p_id,
            insurer=insurer,
            product=product,
            variant=variant,
            policyVersion=policy_ver,
            diagnosis=str(diag_raw),
            normalizedDiagnosis=norm_diag,
            treatment=str(proc_raw),
            normalizedTreatment=norm_proc,
            treatmentDate=datetime.combine(treat_d, datetime.min.time(), tzinfo=timezone.utc) if treat_d else None,
            policyStartDate=datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc) if start_d else None,
            policyAgeMonths=age_months,
            relevantRuleIds=rule_ids,
            aiDecision=str(ai_dec),
            aiExplanation=str(ai_exp),
            deterministicDecision=str(det_dec),
            deterministicReasonCode=str(det_reason),
            finalExpertDecision=None,
            reasonCode=str(det_reason),
            explanation="",
            expertStatus=ExpertReviewStatus.PENDING_EXPERT_REVIEW.value,
            isApprovedForKnowledge=False,
            evidence=ev_items,
        )
        await claim_case.insert()

        # 5. Create immutable v1 analysis snapshot
        v1_id = f"VER-{case_id}-001"
        snapshot = ClaimAnalysisVersion(
            versionId=v1_id,
            caseId=case_id,
            versionNumber=1,
            aiDecision=str(ai_dec),
            deterministicDecision=str(det_dec),
            deterministicReasonCode=str(det_reason),
            confidenceScore=float(coverage_analysis.get("confidence") or 0.0),
            explanation=str(ai_exp),
            ruleResults=det_res.get("ruleResults", []),
        )
        await snapshot.insert()

        logger.info(f"[Case Review] Claim case {case_id} queued for expert review")
        return claim_case

    @classmethod
    async def accept_case(
        cls,
        case_id: str,
        expert_id: str = "expert-reviewer",
        comment: str = "Approved without changes.",
    ) -> ClaimCase:
        """
        Expert accepts the initial AI / deterministic outcome.
        Promotes case to APPROVED and indexes it into ApprovedExpertCase after PII scrubbing.
        """
        case = await ClaimCase.find_one(ClaimCase.case_id == case_id)
        if not case:
            raise ValueError(f"Case with ID '{case_id}' not found.")

        # Update case record
        case.expert_status = ExpertReviewStatus.APPROVED.value
        case.is_approved_for_knowledge = True
        case.final_expert_decision = case.deterministic_decision or case.ai_decision
        case.reason_code = case.deterministic_reason_code or "EXPERT_APPROVED"
        case.explanation = comment
        case.updated_at = datetime.now(timezone.utc)
        await case.save()

        # Log review action
        rev_id = f"REV-{case_id}-{uuid.uuid4().hex[:4].upper()}"
        review = ExpertReview(
            reviewId=rev_id,
            caseId=case_id,
            expertId=expert_id,
            action=ExpertAction.ACCEPT.value,
            comment=comment,
        )
        await review.insert()

        # Index de-identified copy into ApprovedExpertCase
        await cls._index_approved_case(case)
        return case

    @classmethod
    async def correct_case(
        cls,
        case_id: str,
        corrected_decision: str,
        corrected_reason_code: str,
        correction_reason: str,
        expert_comment: str = "",
        expert_id: str = "expert-reviewer",
    ) -> ClaimCase:
        """
        Expert issues a correction.
        Stores an immutable ExpertCorrection record preserving both original and corrected verdicts,
        updates ClaimCase, logs v2 analysis snapshot, and indexes de-identified ApprovedExpertCase.
        """
        case = await ClaimCase.find_one(ClaimCase.case_id == case_id)
        if not case:
            raise ValueError(f"Case with ID '{case_id}' not found.")

        # Log review action
        rev_id = f"REV-{case_id}-{uuid.uuid4().hex[:4].upper()}"
        review = ExpertReview(
            reviewId=rev_id,
            caseId=case_id,
            expertId=expert_id,
            action=ExpertAction.CORRECT.value,
            comment=expert_comment or correction_reason,
        )
        await review.insert()

        # Store detailed correction audit record
        corr_id = f"CORR-{case_id}-{uuid.uuid4().hex[:4].upper()}"
        correction = ExpertCorrection(
            correctionId=corr_id,
            caseId=case_id,
            reviewId=rev_id,
            originalAiDecision=case.ai_decision,
            originalDeterministicDecision=case.deterministic_decision,
            originalReasonCode=case.deterministic_reason_code,
            correctedDecision=corrected_decision,
            correctedReasonCode=corrected_reason_code,
            correctionReason=correction_reason,
            expertComment=expert_comment,
        )
        await correction.insert()

        # Update case with expert outcome
        case.expert_status = ExpertReviewStatus.APPROVED.value
        case.is_approved_for_knowledge = True
        case.final_expert_decision = corrected_decision
        case.reason_code = corrected_reason_code
        case.explanation = f"Expert Correction: {correction_reason}. {expert_comment}".strip()
        case.updated_at = datetime.now(timezone.utc)
        await case.save()

        # Log v2 analysis version snapshot
        v2_id = f"VER-{case_id}-002"
        v2_snapshot = ClaimAnalysisVersion(
            versionId=v2_id,
            caseId=case_id,
            versionNumber=2,
            aiDecision=case.ai_decision,
            deterministicDecision=corrected_decision,
            deterministicReasonCode=corrected_reason_code,
            confidenceScore=100.0,  # Expert ground truth
            explanation=case.explanation,
            ruleResults=[],
        )
        await v2_snapshot.insert()

        # Index de-identified copy into ApprovedExpertCase
        await cls._index_approved_case(case)
        return case

    @classmethod
    async def reject_case(
        cls,
        case_id: str,
        expert_id: str = "expert-reviewer",
        comment: str = "Case invalid or rejected.",
        rejection_reason: Optional[str] = None,
    ) -> ClaimCase:
        """
        Expert rejects the case. Marked REJECTED and strictly excluded from retrieval.
        """
        case = await ClaimCase.find_one(ClaimCase.case_id == case_id)
        if not case:
            raise ValueError(f"Case with ID '{case_id}' not found.")

        final_comment = rejection_reason or comment
        case.expert_status = ExpertReviewStatus.REJECTED.value
        case.is_approved_for_knowledge = False
        case.explanation = final_comment
        case.updated_at = datetime.now(timezone.utc)
        await case.save()

        rev_id = f"REV-{case_id}-{uuid.uuid4().hex[:4].upper()}"
        review = ExpertReview(
            reviewId=rev_id,
            caseId=case_id,
            expertId=expert_id,
            action=ExpertAction.REJECT.value,
            comment=final_comment,
        )
        await review.insert()

        # If it was previously approved, deactivate it
        existing_approved = await ApprovedExpertCase.find_one(ApprovedExpertCase.case_id == case_id)
        if existing_approved:
            existing_approved.status = ExpertReviewStatus.REJECTED.value
            existing_approved.is_active = False
            await existing_approved.save()

        logger.info(f"[Case Review] Claim case {case_id} rejected (excluded from retrieval)")
        return case

    @classmethod
    async def _index_approved_case(cls, case: ClaimCase) -> ApprovedExpertCase:
        """
        De-identifies case, performs duplicate prevention, computes vector embedding,
        and saves into ApprovedExpertCase.
        """
        # 1. Scrub PII
        raw_dict = case.model_dump(by_alias=True)
        deidentified = CaseDeidentifier.deidentify_case_data(raw_dict)

        # 2. Duplicate Detection
        existing_duplicate = await ApprovedExpertCase.find_one({
            "insurer": deidentified["insurer"],
            "product": deidentified["product"],
            "variant": deidentified["variant"],
            "normalizedDiagnosis": deidentified["normalizedDiagnosis"],
            "decision": deidentified["decision"],
            "reasonCode": deidentified["reasonCode"],
            "isActive": True,
        })

        superseded_id = None
        if existing_duplicate and existing_duplicate.case_id != case.case_id:
            # Mark prior duplicate as superseded by latest approved case
            existing_duplicate.status = ExpertReviewStatus.SUPERSEDED.value
            existing_duplicate.is_active = False
            existing_duplicate.updated_at = datetime.now(timezone.utc)
            await existing_duplicate.save()
            superseded_id = existing_duplicate.case_id
            logger.info(f"[Case Review] Duplicate case {existing_duplicate.approved_case_id} superseded")

        # 3. Generate embedding on sanitized text
        semantic_text = build_case_semantic_text(deidentified)
        vector = await cls.embedding_provider.generate_embedding(semantic_text)
        embedding_meta = CaseEmbeddingMetadata(
            semanticText=semantic_text,
            modelName="local-semantic-v1",
            dimension=len(vector),
        )

        app_id = f"EXP-{case.case_id}"
        existing = await ApprovedExpertCase.find_one(ApprovedExpertCase.approved_case_id == app_id)
        if existing:
            await existing.delete()

        approved_doc = ApprovedExpertCase(
            approvedCaseId=app_id,
            caseId=case.case_id,
            insurer=deidentified["insurer"],
            product=deidentified["product"],
            variant=deidentified["variant"],
            policyVersion=deidentified["policyVersion"],
            diagnosis=deidentified["diagnosis"],
            normalizedDiagnosis=deidentified["normalizedDiagnosis"],
            treatment=deidentified["treatment"],
            normalizedTreatment=deidentified["normalizedTreatment"],
            policyAgeMonths=deidentified["policyAgeMonths"],
            applicableRuleTypes=deidentified["applicableRuleTypes"],
            relevantRuleIds=deidentified["relevantRuleIds"],
            decision=deidentified["decision"],
            reasonCode=deidentified["reasonCode"],
            expertExplanation=deidentified["expertExplanation"],
            evidence=[CaseEvidence(**e) for e in deidentified["evidence"]],
            status=ExpertReviewStatus.APPROVED.value,
            isActive=True,
            supersededCaseId=superseded_id,
            embedding=vector,
            embeddingMetadata=embedding_meta,
        )
        await approved_doc.insert()
        logger.info(f"[Case Review] Approved case {app_id} indexed for similar-case search")
        return approved_doc

    @classmethod
    async def get_case_details(cls, case_id: str) -> Optional[Dict[str, Any]]:
        case = await ClaimCase.find_one(ClaimCase.case_id == case_id)
        if not case:
            return None
        reviews = await ExpertReview.find(ExpertReview.case_id == case_id).to_list()
        corrections = await ExpertCorrection.find(ExpertCorrection.case_id == case_id).to_list()
        versions = await ClaimAnalysisVersion.find(ClaimAnalysisVersion.case_id == case_id).to_list()

        return {
            "case": case.model_dump(by_alias=True),
            "versions": [v.model_dump(by_alias=True) for v in versions],
            "reviews": [r.model_dump(by_alias=True) for r in reviews],
            "corrections": [c.model_dump(by_alias=True) for c in corrections],
        }

    # Alias for audit history retrieval
    get_case_history = get_case_details

    @classmethod
    async def list_pending_cases(cls, limit: int = 50, skip: int = 0) -> List[ClaimCase]:
        return await ClaimCase.find(
            ClaimCase.expert_status == ExpertReviewStatus.PENDING_EXPERT_REVIEW.value
        ).skip(skip).limit(limit).to_list()

    @classmethod
    async def list_approved_cases(cls, limit: int = 50, skip: int = 0) -> List[ApprovedExpertCase]:
        return await ApprovedExpertCase.find(
            ApprovedExpertCase.is_active == True,
            ApprovedExpertCase.status == ExpertReviewStatus.APPROVED.value
        ).skip(skip).limit(limit).to_list()
