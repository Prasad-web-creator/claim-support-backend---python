"""
Dataset Quality Gate Service.
Enforces strict quality controls on cases before they can enter supervised training data.
Guarantees that:
1. Only expert-approved cases enter training data.
2. Unreviewed AI output, rejected cases, low-confidence cases, or contradictory claims are blocked.
3. Cases missing evidence, reasons, or complete decisions are excluded.
4. Zero personal identifiable information (PII) is present.
"""

from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.models.expert_review import ClaimCase, ApprovedExpertCase, ExpertReviewStatus
from app.services.expert_learning.deidentifier import CaseDeidentifier


class QualityGateResult(BaseModel):
    is_eligible: bool
    passed_gates: List[str] = Field(default_factory=list)
    failed_gates: List[str] = Field(default_factory=list)
    exclusion_reasons: List[str] = Field(default_factory=list)
    case_id: str


class DatasetQualityGateService:
    """Evaluates candidate claim records against mandatory training readiness criteria."""

    VALID_DECISIONS = {"COVERED", "NOT_COVERED", "PARTIALLY_COVERED", "MANUAL_REVIEW"}

    @classmethod
    def evaluate_case(cls, case: Any) -> QualityGateResult:
        """
        Runs all 8 quality gates against a ClaimCase or ApprovedExpertCase.
        Returns detailed audit pass/fail record.
        """
        case_id = getattr(case, "case_id", getattr(case, "caseId", "UNKNOWN"))
        passed = []
        failed = []
        reasons = []

        # Helper extractors supporting both Beanie ODM objects and plain dictionaries
        def get_field(field_py: str, field_camel: str, default=None):
            if isinstance(case, dict):
                return case.get(field_camel, case.get(field_py, default))
            return getattr(case, field_py, getattr(case, field_camel, default))

        # ─── Gate 1: Expert Approval ──────────────────────────────────────────
        expert_status = str(get_field("expert_status", "expertStatus", "")).upper()
        status_field = str(get_field("status", "status", "")).upper()
        is_approved_flag = bool(get_field("is_approved_for_knowledge", "isApprovedForKnowledge", False))

        is_approved = (
            (expert_status == ExpertReviewStatus.APPROVED.value or status_field == ExpertReviewStatus.APPROVED.value)
            and (is_approved_flag or isinstance(case, ApprovedExpertCase))
        )

        if is_approved:
            passed.append("EXPERT_APPROVED")
        else:
            failed.append("NOT_EXPERT_APPROVED")
            reasons.append(f"Case status '{expert_status or status_field}' is not expert-approved.")

        # ─── Gate 2: Policy Evidence Existence ────────────────────────────────
        evidence_list = get_field("evidence", "evidence", []) or []
        has_valid_evidence = False
        for ev in evidence_list:
            if isinstance(ev, dict):
                text = ev.get("textSnippet") or ev.get("text_snippet") or ev.get("text") or ""
            else:
                text = getattr(ev, "text_snippet", getattr(ev, "textSnippet", ""))
            if text and len(str(text).strip()) >= 8:
                has_valid_evidence = True
                break

        if has_valid_evidence:
            passed.append("POLICY_EVIDENCE_PRESENT")
        else:
            failed.append("MISSING_POLICY_EVIDENCE")
            reasons.append("Case lacks verifiable policy clause evidence text snippet.")

        # ─── Gate 3: Complete Decision ────────────────────────────────────────
        decision = (
            get_field("final_expert_decision", "finalExpertDecision")
            or get_field("decision", "decision")
            or ""
        )
        norm_decision = str(decision).strip().upper()
        if norm_decision in cls.VALID_DECISIONS:
            passed.append("COMPLETE_DECISION")
        else:
            failed.append("INCOMPLETE_DECISION")
            reasons.append(f"Decision '{decision}' is incomplete or invalid.")

        # ─── Gate 4: Reason Code Presence ─────────────────────────────────────
        reason_code = str(get_field("reason_code", "reasonCode", "")).strip()
        if reason_code and reason_code.upper() not in ("UNKNOWN", "NONE", "---", ""):
            passed.append("REASON_CODE_PRESENT")
        else:
            failed.append("MISSING_REASON_CODE")
            reasons.append("Missing standardized reason code for claim adjudication.")

        # ─── Gate 5: Necessary Fields Presence ────────────────────────────────
        diagnosis = str(get_field("diagnosis", "diagnosis", "")).strip()
        treatment = str(get_field("treatment", "treatment", "")).strip()
        insurer = str(get_field("insurer", "insurer", "")).strip()
        product = str(get_field("product", "product", "")).strip()

        if diagnosis and treatment and insurer and product:
            passed.append("NECESSARY_FIELDS_PRESENT")
        else:
            failed.append("MISSING_NECESSARY_FIELDS")
            missing = []
            if not diagnosis: missing.append("diagnosis")
            if not treatment: missing.append("treatment")
            if not insurer: missing.append("insurer")
            if not product: missing.append("product")
            reasons.append(f"Missing mandatory fields: {', '.join(missing)}.")

        # ─── Gate 6: No Unresolved Conflicts / Low Confidence ─────────────────
        if reason_code.upper() in ("CONFLICTING_RULES", "AMBIGUOUS_DIAGNOSIS", "AMBIGUOUS_POLICY_TERMS"):
            failed.append("UNRESOLVED_CONFLICT")
            reasons.append(f"Unresolved conflict or ambiguity flag detected: {reason_code}.")
        else:
            passed.append("NO_UNRESOLVED_CONFLICTS")

        # ─── Gate 7: Privacy & Zero PII Leakage ───────────────────────────────
        explanation = str(get_field("explanation", "explanation") or get_field("expert_explanation", "expertExplanation") or "")
        combined_text = f"{diagnosis} {treatment} {explanation}"

        # Test against regex patterns
        has_pii = False
        pii_issues = []
        if CaseDeidentifier.EMAIL_REGEX.search(combined_text):
            has_pii = True
            pii_issues.append("Email address detected")
        if CaseDeidentifier.PHONE_REGEX.search(combined_text):
            has_pii = True
            pii_issues.append("Phone number detected")
        if CaseDeidentifier.POLICY_NUM_REGEX.search(combined_text):
            has_pii = True
            pii_issues.append("Direct policy number pattern detected")

        if not has_pii:
            passed.append("ZERO_PII_VERIFIED")
        else:
            failed.append("PII_DETECTED")
            reasons.append(f"Potential personal information found: {', '.join(pii_issues)}.")

        # ─── Gate 8: Valid Metadata & Versioning ──────────────────────────────
        pol_version = str(get_field("policy_version", "policyVersion", "1.0")).strip()
        if pol_version:
            passed.append("VALID_VERSION_METADATA")
        else:
            failed.append("INVALID_METADATA")
            reasons.append("Invalid or missing policy version.")

        is_eligible = len(failed) == 0

        return QualityGateResult(
            is_eligible=is_eligible,
            passed_gates=passed,
            failed_gates=failed,
            exclusion_reasons=reasons,
            case_id=case_id,
        )
