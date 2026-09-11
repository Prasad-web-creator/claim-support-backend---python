"""
Deterministic Insurance Coverage Rule Engine Orchestrator.
Executes the strict 13-stage decision workflow, ensures zero hallucinated overrides,
handles rule conflicts, condition normalization, and produces structured audit trails.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.services.rule_engine.models import (
    DeterministicDecision,
    DecisionStatus,
    ReasonCode,
    RuleStatus,
    RuleType,
    RuleCheckResult,
    RuleEvidence,
    NormalizedCondition,
    FinancialImpact,
    RuleAuditTrail,
)
from app.services.rule_engine.condition_normalizer import ConditionNormalizer
from app.services.rule_engine.evaluators.policy_validity import PolicyValidityEvaluator
from app.services.rule_engine.evaluators.exclusion_evaluator import ExclusionEvaluator
from app.services.rule_engine.evaluators.waiting_period import WaitingPeriodEvaluator
from app.services.rule_engine.evaluators.benefit_evaluator import BenefitEvaluator
from app.services.rule_engine.evaluators.limits import LimitsEvaluator
from app.services.rule_engine.evaluators.cost_sharing import CostSharingEvaluator
from app.services.rule_engine.evaluators.network_geography import NetworkGeographyEvaluator
from app.services.rule_engine.conflict_resolver import ConflictResolver

logger = logging.getLogger(__name__)


class CoverageRuleEngine:
    """
    Main entrypoint for deterministic policy rule evaluation.
    Executes rules in strict order:
        1. Policy validity
        2. Claim / treatment date
        3. Condition normalization
        4. Permanent exclusions
        5. Waiting periods
        6. Benefits inclusion
        7. Limits & sub-limits
        8. Co-pay & deductibles
        9. Network / provider requirements
        10. Geography / territorial jurisdiction
        11. Hospitalization duration
        12. Conflict detection & aggregation
        13. Final decision generation
    """

    ENGINE_VERSION = "2.0.0-deterministic"

    @classmethod
    def evaluate(
        cls,
        policy_data: Optional[Dict[str, Any]] = None,
        prescription_data: Optional[Dict[str, Any]] = None,
        policy_json: Optional[Dict[str, Any]] = None,
        prescription_json: Optional[Dict[str, Any]] = None,
        as_of_date: Optional[datetime] = None,
    ) -> DeterministicDecision:
        """
        Executes deterministic evaluation across all 13 decision stages.
        """
        policy_data = policy_data or policy_json or {}
        prescription_data = prescription_data or prescription_json or {}
        eval_dt = (as_of_date.date() if isinstance(as_of_date, datetime) else as_of_date) if as_of_date else None

        all_results: List[RuleCheckResult] = []
        blockers: List[str] = []
        warnings: List[str] = []

        # ──────────────────────────────────────────────────────────────────────
        # Stages 1 & 2: Policy Validity & Claim/Treatment Date
        # ──────────────────────────────────────────────────────────────────────
        val_results = PolicyValidityEvaluator.evaluate(policy_data, prescription_data, as_of_date=eval_dt)
        all_results.extend(val_results)

        # ──────────────────────────────────────────────────────────────────────
        # Stage 3: Normalize Diagnosis / Treatment
        # ──────────────────────────────────────────────────────────────────────
        normalized_conditions = ConditionNormalizer.normalize_claim(prescription_data)

        # Check for Ambiguous Diagnosis
        has_only_ambiguous = (
            len(normalized_conditions) > 0
            and all(c.category == "AMBIGUOUS" or c.confidence < 0.50 for c in normalized_conditions)
        )
        if has_only_ambiguous:
            all_results.append(
                RuleCheckResult(
                    rule_type=RuleType.OTHER_POLICY_SPECIFIC,
                    rule_id="AMB-DIAG-001",
                    condition="AMBIGUOUS_CLINICAL_PRESENTATION",
                    status=RuleStatus.MANUAL_REVIEW,
                    reason_code=ReasonCode.AMBIGUOUS_DIAGNOSIS,
                    description=(
                        f"Ambiguous Diagnosis: Clinical presentation ('{normalized_conditions[0].original_text}') "
                        f"is non-specific. Medical review required to determine definitive illness."
                    ),
                    metadata={"originalText": normalized_conditions[0].original_text},
                )
            )

        # Check for Ambiguous Policy Terms
        if policy_data.get("isAmbiguous") is True or policy_data.get("ambiguousPolicyTerms") is True:
            all_results.append(
                RuleCheckResult(
                    rule_type=RuleType.OTHER_POLICY_SPECIFIC,
                    rule_id="AMB-POL-001",
                    condition="AMBIGUOUS_POLICY_SCHEDULE",
                    status=RuleStatus.MANUAL_REVIEW,
                    reason_code=ReasonCode.AMBIGUOUS_POLICY_TERMS,
                    description="Policy wording contains ambiguous or missing plan schedules. Manual underwriter review required.",
                    metadata={"policyId": policy_data.get("policyId")},
                )
            )

        # ──────────────────────────────────────────────────────────────────────
        # Stage 4: Permanent Exclusions
        # ──────────────────────────────────────────────────────────────────────
        excl_results = ExclusionEvaluator.evaluate(policy_data, prescription_data, normalized_conditions)
        all_results.extend(excl_results)

        # ──────────────────────────────────────────────────────────────────────
        # Stage 5: Waiting Periods
        # ──────────────────────────────────────────────────────────────────────
        wp_results = WaitingPeriodEvaluator.evaluate(policy_data, prescription_data, normalized_conditions)
        all_results.extend(wp_results)

        # ──────────────────────────────────────────────────────────────────────
        # Stage 6: Benefits
        # ──────────────────────────────────────────────────────────────────────
        ben_results = BenefitEvaluator.evaluate(policy_data, prescription_data, normalized_conditions)
        all_results.extend(ben_results)

        # ──────────────────────────────────────────────────────────────────────
        # Stage 7: Limits / Sub-Limits
        # ──────────────────────────────────────────────────────────────────────
        lim_results = LimitsEvaluator.evaluate(policy_data, prescription_data, normalized_conditions)
        all_results.extend(lim_results)

        # Extract base eligible amount after sub-limits (if any applied)
        base_eligible: Optional[float] = None
        for lr in lim_results:
            if lr.financial_impact and "eligibleAmount" in lr.financial_impact:
                base_eligible = float(lr.financial_impact["eligibleAmount"])
                break

        # ──────────────────────────────────────────────────────────────────────
        # Stage 8: Co-Pay / Deductible
        # ──────────────────────────────────────────────────────────────────────
        cost_results = CostSharingEvaluator.evaluate(
            policy_data,
            prescription_data,
            base_eligible_amount=base_eligible,
        )
        all_results.extend(cost_results)

        # ──────────────────────────────────────────────────────────────────────
        # Stages 9, 10 & 11: Network, Geography & Hospitalization Requirements
        # ──────────────────────────────────────────────────────────────────────
        net_geo_results = NetworkGeographyEvaluator.evaluate(policy_data, prescription_data, normalized_conditions)
        all_results.extend(net_geo_results)

        # ──────────────────────────────────────────────────────────────────────
        # Stage 12: Aggregate Results & Conflict Detection
        # ──────────────────────────────────────────────────────────────────────
        custom_conflicts = policy_data.get("ruleConflicts") or policy_data.get("conflictingRules")
        resolved_results, has_conflict = ConflictResolver.detect_and_resolve(
            all_results,
            custom_rule_conflicts=custom_conflicts,
        )

        # ──────────────────────────────────────────────────────────────────────
        # Stage 13: Produce Final Decision
        # ──────────────────────────────────────────────────────────────────────
        final_status = DecisionStatus.COVERED
        final_reason = ReasonCode.POLICY_ACTIVE
        manual_review_required = False
        overall_eligible = True

        # Collect financial breakdowns
        claimed_amt = float(
            prescription_data.get("estimatedCost")
            or prescription_data.get("claimAmount")
            or prescription_data.get("procedureCost")
            or 0.0
        )
        fin_breakdown = FinancialImpact(
            claimed_amount=claimed_amt,
            eligible_amount=claimed_amt,
            sub_limit_applied=None,
            copay_pct=None,
            copay_amount=None,
            deductible_amount=None,
            non_payable_amount=0.0,
            notes=[],
        )

        for res in resolved_results:
            if res.financial_impact:
                if "subLimitApplied" in res.financial_impact:
                    fin_breakdown.sub_limit_applied = res.financial_impact["subLimitApplied"]
                    fin_breakdown.eligible_amount = res.financial_impact["eligibleAmount"]
                    fin_breakdown.non_payable_amount += res.financial_impact.get("nonPayableAmount", 0.0)
                    fin_breakdown.notes.append(res.description)
                if "copayPct" in res.financial_impact:
                    fin_breakdown.copay_pct = res.financial_impact["copayPct"]
                    fin_breakdown.copay_amount = res.financial_impact["copayAmount"]
                    fin_breakdown.eligible_amount = res.financial_impact["eligibleAmount"]
                    fin_breakdown.notes.append(res.description)
                if "deductibleAmount" in res.financial_impact:
                    fin_breakdown.deductible_amount = res.financial_impact["deductibleAmount"]
                    fin_breakdown.eligible_amount = res.financial_impact["eligibleAmount"]
                    fin_breakdown.notes.append(res.description)

        # Determine overall decision priority:
        # CONFLICT / AMBIGUITY -> PERMANENT_EXCLUSION -> WAITING_PERIOD -> POLICY_VALIDITY -> BENEFIT -> LIMIT -> COVERED

        failed_results = [r for r in resolved_results if r.status == RuleStatus.FAILED]
        manual_results = [r for r in resolved_results if r.status == RuleStatus.MANUAL_REVIEW]

        for fr in failed_results:
            blockers.append(fr.description)
        for mr in manual_results:
            warnings.append(mr.description)

        if has_conflict or any(r.reason_code == ReasonCode.CONFLICTING_RULES for r in manual_results):
            final_status = DecisionStatus.MANUAL_REVIEW
            final_reason = ReasonCode.CONFLICTING_RULES
            manual_review_required = True
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.AMBIGUOUS_POLICY_TERMS for r in manual_results):
            final_status = DecisionStatus.MANUAL_REVIEW
            final_reason = ReasonCode.AMBIGUOUS_POLICY_TERMS
            manual_review_required = True
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.AMBIGUOUS_DIAGNOSIS for r in manual_results):
            final_status = DecisionStatus.MANUAL_REVIEW
            final_reason = ReasonCode.AMBIGUOUS_DIAGNOSIS
            manual_review_required = True
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.MISSING_POLICY_DATES for r in manual_results):
            final_status = DecisionStatus.MANUAL_REVIEW
            final_reason = ReasonCode.MISSING_POLICY_DATES
            manual_review_required = True
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.PERMANENT_EXCLUSION for r in failed_results):
            final_status = DecisionStatus.NOT_COVERED
            final_reason = ReasonCode.PERMANENT_EXCLUSION
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.WAITING_PERIOD_ACTIVE for r in failed_results):
            final_status = DecisionStatus.NOT_CURRENTLY_COVERED
            final_reason = ReasonCode.WAITING_PERIOD_ACTIVE
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.POLICY_EXPIRED for r in failed_results):
            final_status = DecisionStatus.NOT_COVERED
            final_reason = ReasonCode.POLICY_EXPIRED
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.POLICY_NOT_YET_EFFECTIVE for r in failed_results):
            final_status = DecisionStatus.NOT_COVERED
            final_reason = ReasonCode.POLICY_NOT_YET_EFFECTIVE
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.BENEFIT_NOT_INCLUDED for r in failed_results):
            final_status = DecisionStatus.NOT_COVERED
            final_reason = ReasonCode.BENEFIT_NOT_INCLUDED
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.GEOGRAPHIC_RESTRICTION for r in failed_results):
            final_status = DecisionStatus.NOT_COVERED
            final_reason = ReasonCode.GEOGRAPHIC_RESTRICTION
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.NETWORK_RESTRICTION for r in manual_results):
            final_status = DecisionStatus.MANUAL_REVIEW
            final_reason = ReasonCode.NETWORK_RESTRICTION
            manual_review_required = True
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.HOSPITALIZATION_CRITERIA_NOT_MET for r in failed_results):
            final_status = DecisionStatus.NOT_COVERED
            final_reason = ReasonCode.HOSPITALIZATION_CRITERIA_NOT_MET
            overall_eligible = False
        elif any(r.reason_code == ReasonCode.LIMIT_EXCEEDED for r in failed_results):
            final_status = DecisionStatus.PARTIALLY_COVERED
            final_reason = ReasonCode.LIMIT_EXCEEDED
            overall_eligible = True
        elif any(r.reason_code == ReasonCode.COPAY_APPLICABLE for r in resolved_results):
            final_status = DecisionStatus.COVERED
            final_reason = ReasonCode.COPAY_APPLICABLE
            overall_eligible = True
        else:
            final_status = DecisionStatus.COVERED
            final_reason = ReasonCode.POLICY_ACTIVE
            overall_eligible = True

        # Construct Audit Trail
        audit_trail = RuleAuditTrail(
            engine_version=cls.ENGINE_VERSION,
            rule_ids_evaluated=[r.rule_id for r in resolved_results if r.rule_id],
            rules_matched=[r.rule_id for r in resolved_results if r.status in (RuleStatus.PASSED, RuleStatus.FAILED, RuleStatus.FLAGGED) and r.rule_id],
            rules_failed=[r.rule_id for r in failed_results if r.rule_id],
            rule_versions={"engine": cls.ENGINE_VERSION},
            policy_version=str(policy_data.get("policyVersion") or "1.0"),
            evaluated_at=datetime.now(timezone.utc),
        )

        decision = DeterministicDecision(
            status=final_status,
            reason_code=final_reason,
            manual_review_required=manual_review_required,
            overall_eligible=overall_eligible,
            blockers=blockers,
            warnings=warnings,
            financials=fin_breakdown,
            normalized_conditions=normalized_conditions,
            rule_results=resolved_results,
            audit=audit_trail,
        )

        logger.info(
            f"[CoverageRuleEngine] Decision produced: {decision.status.value} "
            f"({decision.reason_code.value}) | overall_eligible={overall_eligible} | "
            f"blockers={len(blockers)} | manual_review={manual_review_required}"
        )
        return decision
