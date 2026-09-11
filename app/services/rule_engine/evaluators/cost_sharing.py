"""
Cost-Sharing Evaluator.
Evaluates Co-Payment percentages (e.g. 20% senior citizen or non-preferred zone co-pays)
and Deductible thresholds to calculate net payable amounts.
"""

from typing import Dict, Any, List, Optional

from app.services.rule_engine.models import (
    RuleCheckResult,
    RuleType,
    RuleStatus,
    ReasonCode,
    RuleEvidence,
)


class CostSharingEvaluator:
    """Evaluates policy co-pay and deductible terms to determine insured cost-sharing."""

    @classmethod
    def evaluate(
        cls,
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
        base_eligible_amount: Optional[float] = None,
    ) -> List[RuleCheckResult]:
        results: List[RuleCheckResult] = []

        claimed = float(
            prescription_data.get("estimatedCost")
            or prescription_data.get("claimAmount")
            or prescription_data.get("procedureCost")
            or 0.0
        )
        eval_base = base_eligible_amount if base_eligible_amount is not None else claimed

        # 1. Co-Payment Evaluation
        copay_raw = policy_data.get("copayPct") or policy_data.get("coPay") or policy_data.get("copay")
        copay_pct = 0.0

        if copay_raw is not None:
            try:
                import re
                digits = re.findall(r"\d+", str(copay_raw))
                if digits:
                    copay_pct = float(digits[0])
            except Exception:
                copay_pct = 0.0

        if copay_pct > 0:
            copay_deduction = eval_base * (copay_pct / 100.0)
            net_eligible = max(0.0, eval_base - copay_deduction)

            results.append(
                RuleCheckResult(
                    rule_type=RuleType.CO_PAY,
                    rule_id="COST-COPAY-001",
                    status=RuleStatus.PASSED,
                    reason_code=ReasonCode.COPAY_APPLICABLE,
                    description=(
                        f"Co-Pay Applicable: Policy applies a {copay_pct:.0f}% co-payment. "
                        f"Insured share: INR {copay_deduction:,.2f}; Net insurer payable: INR {net_eligible:,.2f}."
                    ),
                    evidence=RuleEvidence(
                        source_document=policy_data.get("sourceDocument") or "Policy_Schedule.pdf",
                        page=1,
                        section="Co-Payment Schedule",
                        text_snippet=f"Mandatory co-payment of {copay_pct:.0f}% on all admissible claims.",
                    ),
                    financial_impact={
                        "claimedAmount": claimed,
                        "copayPct": copay_pct,
                        "copayAmount": copay_deduction,
                        "eligibleAmount": net_eligible,
                    },
                    metadata={"copayPct": copay_pct},
                )
            )

        # 2. Deductible Evaluation
        deductible_raw = policy_data.get("deductibles") or policy_data.get("deductible")
        deductible_amt = 0.0

        if deductible_raw is not None:
            try:
                import re
                clean = re.sub(r"[^\d.]", "", str(deductible_raw))
                if clean:
                    deductible_amt = float(clean)
            except Exception:
                deductible_amt = 0.0

        if deductible_amt > 0:
            deductible_applied = min(eval_base, deductible_amt)
            net_after_deductible = max(0.0, eval_base - deductible_applied)

            results.append(
                RuleCheckResult(
                    rule_type=RuleType.DEDUCTIBLE,
                    rule_id="COST-DEDUCT-001",
                    status=RuleStatus.PASSED,
                    reason_code=ReasonCode.DEDUCTIBLE_APPLICABLE,
                    description=(
                        f"Deductible Applicable: Policy carries an aggregate deductible of INR {deductible_amt:,.0f}. "
                        f"Deductible applied: INR {deductible_applied:,.0f}; Net payable: INR {net_after_deductible:,.0f}."
                    ),
                    evidence=RuleEvidence(
                        source_document=policy_data.get("sourceDocument") or "Policy_Schedule.pdf",
                        page=1,
                        section="Deductible Clause",
                        text_snippet=f"Aggregate deductible of INR {deductible_amt:,.0f}.",
                    ),
                    financial_impact={
                        "claimedAmount": claimed,
                        "deductibleAmount": deductible_applied,
                        "eligibleAmount": net_after_deductible,
                    },
                    metadata={"deductibleInr": deductible_amt},
                )
            )

        return results
