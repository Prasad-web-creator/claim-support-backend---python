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
from app.services.rule_engine.policy_exclusions import (
    claim_texts,
    find_exclusion_clause,
    find_schedule_not_covered,
    match_policy_exclusion,
    policy_exclusion_entries,
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

        # 2. Evaluate against the exclusions written in the customer's own policy
        contract_result = cls._evaluate_policy_exclusions(policy_data, prescription_data)
        if contract_result:
            results.append(contract_result)

        return results

    @staticmethod
    def _evaluate_policy_exclusions(
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
    ) -> Optional[RuleCheckResult]:
        """
        Matches the claim against excludedDiseases / excludedTreatments / exclusions and
        quotes the policy clause that states the exclusion.

        The description is "<reason> - <policy wording>" so the report can show the
        reason and the quoted wording separately.
        """
        match = match_policy_exclusion(claim_texts(prescription_data), policy_exclusion_entries(policy_data))
        if not match:
            return None

        policy_text = policy_data.get("policyText")
        claimed = match["claimText"]
        clause = find_exclusion_clause(policy_text, match["term"])
        schedule_row = find_schedule_not_covered(policy_text, match["term"])

        if clause:
            clause_ref = f"{clause['section']} {clause['title']}"
            reason = f"{claimed} is permanently excluded under policy clause {clause_ref}"
            wording = f"{clause_ref}: \"{clause['wording']}\""
        else:
            clause_ref = "Policy Exclusions"
            reason = f"{claimed} is permanently excluded under this policy (listed exclusion: \"{match['entry']}\")"
            wording = f"Policy exclusion: \"{match['entry']}\""
        if schedule_row:
            reason += f", and the policy schedule states \"{schedule_row}\""
            wording += f" Policy schedule: \"{schedule_row}\"."
        reason += ". The waiting period does not apply because this treatment is never covered under the policy."
        # " - " separates reason from wording, so it must not appear inside the reason.
        reason = reason.replace(" - ", " – ")

        return RuleCheckResult(
            rule_type=RuleType.PERMANENT_EXCLUSION,
            rule_id=f"EXCL-CONTRACT-{clause['code'].upper().replace(' ', '')}" if clause and clause["code"] else "EXCL-CONTRACT-001",
            condition=match["term"].upper(),
            status=RuleStatus.FAILED,
            reason_code=ReasonCode.PERMANENT_EXCLUSION,
            description=f"{reason} - {wording}",
            evidence=RuleEvidence(
                source_document=policy_data.get("sourceDocument") or "Policy_Document.pdf",
                section=clause_ref,
                clause_id=clause["code"] if clause else "",
                text_snippet=clause["wording"] if clause else match["entry"],
                authority_level="CONTRACTUAL_POLICY_WORDING",
            ),
            metadata={
                "excludedEntry": match["entry"],
                "matchedTerm": match["term"],
                "originalText": claimed,
                "scheduleRow": schedule_row,
            },
        )
