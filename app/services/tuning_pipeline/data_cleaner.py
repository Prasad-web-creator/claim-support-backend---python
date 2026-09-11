"""
Training Data Cleaner and Anonymous Mapper.
Maps raw approved database records into standardized, de-identified TrainingExample objects.
Guarantees:
- Pure anonymization: No internal MongoDB IDs, user IDs, session IDs, or real names.
- Deterministic synthetic IDs (e.g. TUNE-EX-0001, EVAL-EX-0001).
- Automatic categorization into the 12 evaluation categories.
"""

import re
from typing import Dict, Any, List, Optional
from app.models.dataset_tuning import (
    TrainingExample,
    TrainingExampleInput,
    TrainingExpectedOutput,
    EvidenceCitation,
    EvaluationCategory,
    DatasetSplitType,
)
from app.services.expert_learning.deidentifier import CaseDeidentifier


class TrainingDataCleaner:
    """Sanitizes and transforms approved claim cases into model training format."""

    @classmethod
    def map_category(cls, reason_code: str, decision: str) -> EvaluationCategory:
        """Categorizes a case into one of the 12 standardized evaluation categories."""
        rc = (reason_code or "").upper()
        dec = (decision or "").upper()

        if "WAITING_PERIOD_ACTIVE" in rc:
            return EvaluationCategory.WAITING_PERIOD_ACTIVE
        elif "WAITING_PERIOD" in rc and "COMPLETED" in rc:
            return EvaluationCategory.WAITING_PERIOD_COMPLETED
        elif "PERMANENT_EXCLUSION" in rc or "EXCLUSION" in rc:
            return EvaluationCategory.PERMANENT_EXCLUSION
        elif "BENEFIT_NOT_INCLUDED" in rc or "BENEFIT_UNAVAILABLE" in rc:
            return EvaluationCategory.BENEFIT_UNAVAILABLE
        elif "LIMIT_EXCEEDED" in rc or "SUB_LIMIT" in rc:
            return EvaluationCategory.LIMIT_EXCEEDED
        elif "NETWORK" in rc:
            return EvaluationCategory.NETWORK_MISMATCH
        elif "GEOGRAPHIC" in rc:
            return EvaluationCategory.GEOGRAPHICAL_RESTRICTION
        elif "CONFLICTING" in rc:
            return EvaluationCategory.CONFLICTING_RULES
        elif "AMBIGUOUS" in rc:
            return EvaluationCategory.AMBIGUOUS_CASE
        elif dec == "MANUAL_REVIEW" or "MANUAL" in rc:
            return EvaluationCategory.MANUAL_REVIEW
        elif "COMPLEX" in rc or "COPAY" in rc or "DEDUCTIBLE" in rc:
            return EvaluationCategory.COMPLEX_CLAIM
        elif dec == "COVERED":
            return EvaluationCategory.COVERED
        return EvaluationCategory.COVERED

    @classmethod
    def clean_and_format_example(
        cls,
        case: Any,
        index: int,
        split: DatasetSplitType = DatasetSplitType.TRAIN,
    ) -> TrainingExample:
        """
        Transforms an approved database case into a pristine de-identified TrainingExample.
        """
        # Field getter helper
        def get_field(field_py: str, field_camel: str, default=None):
            if isinstance(case, dict):
                return case.get(field_camel, case.get(field_py, default))
            return getattr(case, field_py, getattr(case, field_camel, default))

        prefix = "EVAL" if split == DatasetSplitType.TEST else "TUNE"
        example_id = f"{prefix}-EX-{index:04d}"

        # Clean strings
        diag = CaseDeidentifier.scrub_text(str(get_field("diagnosis", "diagnosis", "")))
        norm_diag = str(get_field("normalized_diagnosis", "normalizedDiagnosis", "GENERAL"))
        treat = CaseDeidentifier.scrub_text(str(get_field("treatment", "treatment", "")))
        norm_treat = str(get_field("normalized_treatment", "normalizedTreatment", ""))

        insurer = str(get_field("insurer", "insurer", "Standard Provider"))
        product = str(get_field("product", "product", "Comprehensive Health Plan"))
        variant = str(get_field("variant", "variant", "Standard"))
        policy_ver = str(get_field("policy_version", "policyVersion", "1.0"))
        policy_age = get_field("policy_age_months", "policyAgeMonths", None)

        rules = get_field("applicable_rule_types", "applicableRuleTypes", []) or []
        decision = str(get_field("decision", "decision") or get_field("final_expert_decision", "finalExpertDecision", "COVERED")).upper()
        reason_code = str(get_field("reason_code", "reasonCode", "POLICY_ACTIVE"))
        explanation = CaseDeidentifier.scrub_text(
            str(get_field("expert_explanation", "expertExplanation") or get_field("explanation", "explanation", ""))
        )

        # Evidence cleaning
        raw_evidence = get_field("evidence", "evidence", []) or []
        cleaned_evidence: List[EvidenceCitation] = []
        for ev in raw_evidence:
            if isinstance(ev, dict):
                doc_name = ev.get("documentName") or ev.get("sourceDocument") or "Policy_Document.pdf"
                page = ev.get("pageNumber") or ev.get("page")
                sec = ev.get("section") or ""
                cid = ev.get("clauseId") or ""
                txt = CaseDeidentifier.scrub_text(ev.get("textSnippet") or ev.get("text") or "")
                auth = ev.get("authorityLevel") or "CONTRACTUAL_POLICY_WORDING"
            else:
                doc_name = getattr(ev, "document_name", getattr(ev, "documentName", "Policy_Document.pdf"))
                page = getattr(ev, "page_number", getattr(ev, "pageNumber", None))
                sec = getattr(ev, "section", "")
                cid = getattr(ev, "clause_id", getattr(ev, "clauseId", ""))
                txt = CaseDeidentifier.scrub_text(getattr(ev, "text_snippet", getattr(ev, "textSnippet", "")))
                auth = getattr(ev, "authority_level", getattr(ev, "authorityLevel", "CONTRACTUAL_POLICY_WORDING"))

            if txt:
                cleaned_evidence.append(EvidenceCitation(
                    documentName=doc_name,
                    pageNumber=page,
                    section=sec,
                    clauseId=cid,
                    textSnippet=txt,
                    authorityLevel=auth,
                ))

        category = cls.map_category(reason_code, decision)

        inp = TrainingExampleInput(
            diagnosis=diag,
            normalizedDiagnosis=norm_diag,
            treatment=treat,
            normalizedTreatment=norm_treat,
            policyDurationMonths=policy_age,
            insurer=insurer,
            product=product,
            variant=variant,
            policyVersion=policy_ver,
            applicableRules=rules,
            retrievedEvidence=cleaned_evidence,
        )

        out = TrainingExpectedOutput(
            status=decision,
            reasonCode=reason_code,
            ruleResults=[],
            evidence=cleaned_evidence,
            explanation=explanation,
            manualReviewRequired=(decision == "MANUAL_REVIEW"),
            confidence=100,
        )

        return TrainingExample(
            exampleId=example_id,
            sourceCaseId=str(get_field("case_id", "caseId", "")),
            category=category,
            split=split,
            input=inp,
            expectedOutput=out,
            metadata={
                "category": category.value,
                "split": split.value,
            }
        )
