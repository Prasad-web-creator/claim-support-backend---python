"""
Limits & Sub-Limits Evaluator.
Evaluates claims against policy Sum Insured, Room Category capping (proportionate deductions),
and Modern Treatment procedure sub-limits (e.g. Classic tier INR 1,00,000 cap).
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


class LimitsEvaluator:
    """Evaluates sub-limits, room rent eligibility, and Sum Insured boundaries."""

    @classmethod
    def evaluate(
        cls,
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
        normalized_conditions: List[NormalizedCondition],
    ) -> List[RuleCheckResult]:
        results: List[RuleCheckResult] = []

        variant = (
            policy_data.get("variant")
            or policy_data.get("policyType")
            or policy_data.get("planTier")
            or "All"
        ).strip().lower()

        # 1. Modern Treatment / Robotic Surgery Sub-Limit
        has_robotic = any(n.normalized_value == "ROBOTIC_SURGERY" for n in normalized_conditions)
        claimed_cost = float(
            prescription_data.get("estimatedCost")
            or prescription_data.get("claimAmount")
            or prescription_data.get("procedureCost")
            or prescription_data.get("billAmount")
            or 0.0
        )

        if has_robotic:
            # Classic variant has INR 1,00,000 sub-limit for Modern Treatments
            is_classic = variant in ("classic", "all", "")
            sub_limit = 100000.0 if is_classic else None

            if sub_limit and claimed_cost > sub_limit:
                excess = claimed_cost - sub_limit
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.SUB_LIMIT,
                        rule_id="LIM-MODERN-CLASSIC",
                        condition="ROBOTIC_SURGERY",
                        status=RuleStatus.FAILED,
                        reason_code=ReasonCode.LIMIT_EXCEEDED,
                        description=(
                            f"Modern Treatment Sub-Limit Exceeded: Claim of INR {claimed_cost:,.0f} exceeds "
                            f"the Classic variant sub-limit of INR {sub_limit:,.0f} per claim. "
                            f"Eligible amount capped at INR {sub_limit:,.0f} (Excess non-payable: INR {excess:,.0f})."
                        ),
                        evidence=RuleEvidence(
                            source_document="Leaflet artwork.pdf",
                            page=1,
                            section="Variant Comparison Table — Modern Treatments",
                            clause_id="LIM-MODERN-TREATMENT",
                            text_snippet="Modern Treatments: Classic = Up to 1 Lac, Select/Elite/Black = Up to SI.",
                            authority_level="REFERENCE_BENCHMARK",
                        ),
                        financial_impact={
                            "claimedAmount": claimed_cost,
                            "subLimitApplied": sub_limit,
                            "eligibleAmount": sub_limit,
                            "nonPayableAmount": excess,
                        },
                        metadata={"variant": variant, "subLimitInr": sub_limit},
                    )
                )
            elif sub_limit:
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.SUB_LIMIT,
                        rule_id="LIM-MODERN-CLASSIC",
                        condition="ROBOTIC_SURGERY",
                        status=RuleStatus.PASSED,
                        reason_code=ReasonCode.WITHIN_LIMITS,
                        description=f"Modern Treatment cost (INR {claimed_cost:,.0f}) is within the sub-limit (INR {sub_limit:,.0f}).",
                        evidence=RuleEvidence(
                            source_document="Leaflet artwork.pdf",
                            page=1,
                            section="Variant Comparison Table — Modern Treatments",
                        ),
                        financial_impact={"claimedAmount": claimed_cost, "eligibleAmount": claimed_cost},
                        metadata={"variant": variant},
                    )
                )

        # 2. Room Rent Eligibility & Proportionate Deduction Check
        room_requested = (
            prescription_data.get("roomCategory")
            or prescription_data.get("roomType")
            or ""
        ).strip().lower()

        if room_requested:
            if "classic" in variant and "general" not in room_requested:
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.LIMIT,
                        rule_id="LIM-ROOM-001",
                        condition="ROOM_RENT_CAPPING",
                        status=RuleStatus.FLAGGED,
                        reason_code=ReasonCode.LIMIT_EXCEEDED,
                        description=(
                            f"Room Category Capping: Insured selected '{room_requested}', but Classic variant covers "
                            f"'General room' only. Proportionate deduction applies to associated medical charges."
                        ),
                        evidence=RuleEvidence(
                            source_document="Policy_Document.pdf",
                            page=1,
                            section="Base Coverage — Room Rent",
                            text_snippet="General Room accommodation only. Admission to higher room attracts proportionate deduction.",
                        ),
                        metadata={"requestedRoom": room_requested, "eligibleRoom": "General room"},
                    )
                )

        return results
