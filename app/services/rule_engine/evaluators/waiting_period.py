"""
Waiting Period Evaluator.
Evaluates claims against initial 30-day waiting periods, 2-year specific illness
waiting periods, and pre-existing disease (PED) waiting periods using
exact calendar month arithmetic.
"""

from datetime import date
from typing import Dict, Any, List, Optional

from app.services.rule_engine.models import (
    RuleCheckResult,
    RuleType,
    RuleStatus,
    ReasonCode,
    RuleEvidence,
    NormalizedCondition,
)
from app.services.rule_engine.date_utils import (
    parse_flexible_date,
    completed_calendar_months,
    days_between,
)


class WaitingPeriodEvaluator:
    """Evaluates whether all applicable waiting periods have been completed prior to treatment."""

    # 14 Statutory 2-Year Specific Waiting Periods from 2 YEAR & Permanent Exclusion-2.pdf (Page 1)
    SPECIFIC_WAITING_CONDITIONS = {
        "CATARACT": {
            "title": "Cataract",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #1",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Standard 24-month specific waiting period applies for Cataract surgery.",
        },
        "CHOLELITHIASIS": {
            "title": "Stones in Biliary and Urinary Systems",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #2",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Treatment for Gallbladder stones (Cholelithiasis) and urinary calculi requires 24 months continuous coverage.",
        },
        "HERNIA": {
            "title": "Hernia of All Types",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #3",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Herniorrhaphy and hernioplasty for all types of hernia are subject to 24-month waiting period.",
        },
        "HYDROCELE": {
            "title": "Hydrocele",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #5",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Hydrocele and varicocele surgeries are excluded for the first 24 months of coverage.",
        },
        "FISTULA_PILES": {
            "title": "Piles, Fistula and Fissure in Ano",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #6",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Anal fissure, hemorrhoids (piles), and fistula in ano are subject to 24-month waiting period.",
        },
        "TONSILS_ADENOIDS": {
            "title": "Surgery on Tonsils / Adenoids",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #4",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Tonsillectomy and adenoidectomy are subject to 24 months waiting period.",
        },
        "VARICOSE_VEINS": {
            "title": "Varicose Veins",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #10",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Endovenous laser and surgical stripping for varicose veins require 24 months continuous coverage.",
        },
        "KNEE_ARTHROPLASTY": {
            "title": "Osteoarthritis and Joint Replacement",
            "requiredMonths": 24,
            "section": "Specific 2-Year Waiting Period #9",
            "sourceDocument": "2 YEAR & Permanent Exclusion-2.pdf",
            "page": 1,
            "description": "Joint replacement surgery and treatment of non-infective arthritis are excluded for first 24 months.",
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

        start_date = parse_flexible_date(
            policy_data.get("policyStartDate")
            or policy_data.get("policyEffectiveDate")
            or policy_data.get("effectiveFrom")
        )
        treatment_date = parse_flexible_date(
            prescription_data.get("visitDate")
            or prescription_data.get("consultationDate")
            or prescription_data.get("treatmentDate")
        )

        if not start_date or not treatment_date:
            # Cannot evaluate waiting period without dates; handled in policy validity
            return results

        completed_months = completed_calendar_months(start_date, treatment_date)
        elapsed_days = days_between(start_date, treatment_date)

        # 1. Pre-Existing Disease (PED) Check
        is_ped = prescription_data.get("isPreExisting") is True
        diag_date = parse_flexible_date(
            prescription_data.get("diagnosisDate")
            or prescription_data.get("diseaseOnsetDate")
            or prescription_data.get("symptomOnsetDate")
        )
        if diag_date and diag_date < start_date:
            is_ped = True

        if is_ped:
            # Default PED waiting period is typically 36 months unless specified otherwise
            ped_months = int(policy_data.get("pedWaitingPeriodMonths") or 36)
            passed = completed_months >= ped_months
            results.append(
                RuleCheckResult(
                    rule_type=RuleType.WAITING_PERIOD,
                    rule_id="WP-PED-001",
                    condition="PRE_EXISTING_DISEASE",
                    required_months=ped_months,
                    completed_months=completed_months,
                    status=RuleStatus.PASSED if passed else RuleStatus.FAILED,
                    reason_code=ReasonCode.WAITING_PERIOD_COMPLETED if passed else ReasonCode.WAITING_PERIOD_ACTIVE,
                    description=(
                        f"Pre-Existing Disease (PED) waiting period satisfied: Policy active for {completed_months} full months "
                        f"(Required: {ped_months} months)."
                        if passed
                        else f"Pre-Existing Disease (PED) waiting period ACTIVE: Policy active for {completed_months} full months "
                        f"(Required: {ped_months} months). Claim is not currently payable."
                    ),
                    evidence=RuleEvidence(
                        source_document=policy_data.get("sourceDocument") or "Policy_Document.pdf",
                        page=1,
                        section="Pre-Existing Disease Waiting Period Clause",
                        text_snippet=f"PED Waiting Period: {ped_months} months from inception.",
                        authority_level="CONTRACTUAL_POLICY_WORDING",
                    ),
                    metadata={"completedMonths": completed_months, "requiredMonths": ped_months, "isPED": True},
                )
            )

        # 2. Specific 2-Year Waiting Period Checks
        for norm in normalized_conditions:
            code = norm.normalized_value
            if code in cls.SPECIFIC_WAITING_CONDITIONS:
                meta = cls.SPECIFIC_WAITING_CONDITIONS[code]
                req_months = meta["requiredMonths"]
                passed = completed_months >= req_months

                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.WAITING_PERIOD,
                        rule_id=f"WP-SPECIFIC-{code}",
                        condition=code,
                        required_months=req_months,
                        completed_months=completed_months,
                        status=RuleStatus.PASSED if passed else RuleStatus.FAILED,
                        reason_code=ReasonCode.WAITING_PERIOD_COMPLETED if passed else ReasonCode.WAITING_PERIOD_ACTIVE,
                        description=(
                            f"Specific waiting period satisfied for {meta['title']}: Policy active for {completed_months} full months "
                            f"(Required: {req_months} months)."
                            if passed
                            else f"Specific waiting period ACTIVE for {meta['title']}: Policy active for {completed_months} full months "
                            f"(Required: {req_months} months). Condition is NOT currently covered."
                        ),
                        evidence=RuleEvidence(
                            source_document=meta["sourceDocument"],
                            page=meta["page"],
                            section=meta["section"],
                            clause_id=f"WP-{code}",
                            text_snippet=meta["description"],
                            authority_level="REFERENCE_BENCHMARK",
                        ),
                        metadata={
                            "condition": code,
                            "completedMonths": completed_months,
                            "requiredMonths": req_months,
                            "originalText": norm.original_text,
                        },
                    )
                )

        # 3. Initial 30-Day Waiting Period (for non-accidental illnesses during first month)
        is_accident = (
            "accident" in (prescription_data.get("diagnosis") or "").lower()
            or prescription_data.get("isAccidental") is True
        )
        if not is_accident and elapsed_days < 30:
            results.append(
                RuleCheckResult(
                    rule_type=RuleType.WAITING_PERIOD,
                    rule_id="WP-INITIAL-30D",
                    condition="INITIAL_ILLNESS",
                    required_months=1,
                    completed_months=0,
                    status=RuleStatus.FAILED,
                    reason_code=ReasonCode.WAITING_PERIOD_ACTIVE,
                    description=f"Initial 30-day waiting period active: Claim occurred {elapsed_days} days after inception (Required: 30 days).",
                    evidence=RuleEvidence(
                        source_document="Standard_Terms.pdf",
                        page=1,
                        section="Initial Waiting Period (30 Days)",
                        text_snippet="30 days waiting period for all illnesses except accidental injuries.",
                    ),
                    metadata={"elapsedDays": elapsed_days, "requiredDays": 30},
                )
            )

        return results
