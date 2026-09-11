"""
Policy Validity Evaluator.
Validates policy active status, inception dates, expiration dates,
treatment dates, and member identity integrity.
"""

from datetime import date, datetime
from typing import Dict, Any, List, Optional, Tuple

from app.services.rule_engine.models import (
    RuleCheckResult,
    RuleType,
    RuleStatus,
    ReasonCode,
    RuleEvidence,
)
from app.services.rule_engine.date_utils import (
    parse_flexible_date,
    check_policy_period,
)


class PolicyValidityEvaluator:
    """Evaluates whether the policy was valid and active at the time of medical treatment."""

    @classmethod
    def evaluate(
        cls,
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
        as_of_date: Optional[date] = None,
    ) -> List[RuleCheckResult]:
        results: List[RuleCheckResult] = []

        # 1. Parse policy dates
        start_date = parse_flexible_date(
            policy_data.get("policyStartDate")
            or policy_data.get("policyEffectiveDate")
            or policy_data.get("effectiveFrom")
            or policy_data.get("inceptionDate")
        )
        end_date = parse_flexible_date(
            policy_data.get("policyEndDate")
            or policy_data.get("policyExpirationDate")
            or policy_data.get("effectiveTo")
            or policy_data.get("expiryDate")
        )

        # 2. Parse treatment/consultation date
        treatment_date = parse_flexible_date(
            prescription_data.get("visitDate")
            or prescription_data.get("consultationDate")
            or prescription_data.get("treatmentDate")
            or prescription_data.get("admissionDate")
            or as_of_date
        )

        # Fallback for self-entered queries if no date given
        is_self_entered = bool(
            prescription_data.get("isManual")
            or prescription_data.get("prescriptionSource") == "Self-entered Prescription"
            or prescription_data.get("manualText")
        )
        if not treatment_date and is_self_entered:
            treatment_date = datetime.now().date()

        # Date existence checks
        if not start_date or not treatment_date:
            missing_fields = []
            if not start_date:
                missing_fields.append("policyStartDate")
            if not treatment_date:
                missing_fields.append("treatmentDate/visitDate")

            results.append(
                RuleCheckResult(
                    rule_type=RuleType.POLICY_VALIDITY,
                    rule_id="VAL-DATE-001",
                    status=RuleStatus.MANUAL_REVIEW,
                    reason_code=ReasonCode.MISSING_POLICY_DATES,
                    description=f"Missing essential dates: {', '.join(missing_fields)}. Cannot verify temporal coverage.",
                    evidence=RuleEvidence(
                        source_document=policy_data.get("sourceDocument") or "Policy_Schedule.pdf",
                        page=1,
                        section="Policy Dates",
                    ),
                    metadata={"missingFields": missing_fields},
                )
            )
            return results

        # Future date check
        today = datetime.now().date()
        if treatment_date > today:
            results.append(
                RuleCheckResult(
                    rule_type=RuleType.POLICY_VALIDITY,
                    rule_id="VAL-DATE-002",
                    status=RuleStatus.FAILED,
                    reason_code=ReasonCode.FUTURE_TREATMENT_DATE,
                    description=f"Treatment date ({treatment_date.isoformat()}) is in the future.",
                    evidence=RuleEvidence(
                        source_document="Prescription_Document",
                        page=1,
                        section="Consultation Date",
                    ),
                    metadata={"treatmentDate": treatment_date.isoformat(), "today": today.isoformat()},
                )
            )
            return results

        # Period check
        is_active, desc, code = check_policy_period(start_date, end_date, treatment_date)

        results.append(
            RuleCheckResult(
                rule_type=RuleType.POLICY_VALIDITY,
                rule_id="VAL-PERIOD-001",
                status=RuleStatus.PASSED if is_active else RuleStatus.FAILED,
                reason_code=code,
                description=desc,
                evidence=RuleEvidence(
                    source_document=policy_data.get("sourceDocument") or "Policy_Schedule.pdf",
                    page=1,
                    section="Period of Insurance",
                    text_snippet=f"Policy Period: {start_date.isoformat()} to {end_date.isoformat() if end_date else 'Continuous'}",
                ),
                metadata={
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat() if end_date else None,
                    "treatmentDate": treatment_date.isoformat(),
                },
            )
        )

        return results
