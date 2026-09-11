"""
Permanent Exclusion Evaluator.
Evaluates claims against statutory IRDAI non-payable exclusions and
policy-specific permanently excluded illnesses/treatments.
"""

from typing import Dict, Any, List, Optional

from app.services.rule_engine.models import (
    RuleCheckResult,
    RuleType,
    RuleStatus,
    ReasonCode,
    RuleEvidence,
    NormalizedCondition,
)


class ExclusionEvaluator:
    """Evaluates whether any claimed illness or procedure falls under permanent exclusions."""

    # Statutory permanent exclusions mapping
    STATUTORY_PERMANENT_EXCLUSIONS = {
        "COSMETIC_SURGERY": {
            "exclusionNumber": 5,
            "title": "Cosmetic or Plastic Surgery",
            "description": "Expenses for cosmetic surgery or aesthetic treatment are permanently excluded.",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 2,
            "section": "Statutory Permanent Exclusions #5",
        },
        "OBESITY_TREATMENT": {
            "exclusionNumber": 6,
            "title": "Obesity and Weight Control",
            "description": "Surgical treatment of obesity or weight loss procedures are permanently excluded.",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 2,
            "section": "Statutory Permanent Exclusions #6",
        },
        "CHANGE_OF_GENDER": {
            "exclusionNumber": 4,
            "title": "Change-of-Gender Treatments",
            "description": "Expenses relating to sex reassignment or gender affirmation treatments are permanently non-payable.",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 2,
            "section": "Statutory Permanent Exclusions #4",
        },
        "DENTAL_TREATMENT": {
            "exclusionNumber": 20,
            "title": "Routine Dental / Oral Treatment",
            "description": "Dental surgeries and orthodontic procedures are excluded unless requiring inpatient care due to accidental injury.",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 2,
            "section": "Statutory Permanent Exclusions #20",
        },
        "UNPROVEN_TREATMENT": {
            "exclusionNumber": 13,
            "title": "Unproven & Experimental Treatments",
            "description": "Experimental or unproven pharmacological or surgical treatments are permanently excluded.",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 2,
            "section": "Statutory Permanent Exclusions #13",
        },
    }

    @classmethod
    def evaluate(
        cls,
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
        normalized_conditions: List[NormalizedCondition],
    ) -> List[RuleCheckResult]:
        results: List[RuleCheckResult] = []

        # 1. Evaluate against Statutory Permanent Exclusions
        for norm in normalized_conditions:
            code = norm.normalized_value
            if code in cls.STATUTORY_PERMANENT_EXCLUSIONS:
                excl_meta = cls.STATUTORY_PERMANENT_EXCLUSIONS[code]
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.PERMANENT_EXCLUSION,
                        rule_id=f"EXCL-STAT-{excl_meta['exclusionNumber']:03d}",
                        condition=norm.normalized_value,
                        status=RuleStatus.FAILED,
                        reason_code=ReasonCode.PERMANENT_EXCLUSION,
                        description=f"Permanently Excluded: {excl_meta['title']} ({norm.original_text}) is non-payable under statutory exclusions.",
                        evidence=RuleEvidence(
                            source_document=excl_meta["sourceDocument"],
                            page=excl_meta["page"],
                            section=excl_meta["section"],
                            clause_id=f"EXCL-{excl_meta['exclusionNumber']}",
                            text_snippet=excl_meta["description"],
                            authority_level="REFERENCE_BENCHMARK",
                        ),
                        metadata={"originalText": norm.original_text, "source": norm.source},
                    )
                )

        # 2. Evaluate against Policy Contractual Excluded Diseases List
        excluded_diseases = [
            str(d).strip().lower()
            for d in (policy_data.get("excludedDiseases") or policy_data.get("exclusions") or [])
            if d
        ]

        if excluded_diseases:
            for norm in normalized_conditions:
                text_lower = norm.original_text.lower()
                norm_lower = norm.normalized_value.lower()
                for excl in excluded_diseases:
                    if excl in text_lower or text_lower in excl or excl in norm_lower:
                        results.append(
                            RuleCheckResult(
                                rule_type=RuleType.PERMANENT_EXCLUSION,
                                rule_id="EXCL-CONTRACT-001",
                                condition=norm.normalized_value,
                                status=RuleStatus.FAILED,
                                reason_code=ReasonCode.PERMANENT_EXCLUSION,
                                description=f"Contractual Exclusion: Diagnosis '{norm.original_text}' is explicitly listed in policy exclusions list.",
                                evidence=RuleEvidence(
                                    source_document=policy_data.get("sourceDocument") or "Policy_Document.pdf",
                                    page=1,
                                    section="Schedule of Exclusions",
                                    text_snippet=f"Explicitly excluded illness: {excl}",
                                    authority_level="CONTRACTUAL_POLICY_WORDING",
                                ),
                                metadata={"excludedDisease": excl, "originalText": norm.original_text},
                            )
                        )

        return results
