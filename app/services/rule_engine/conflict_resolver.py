"""
Conflict Resolver for Policy Rules.
Detects contradictory rules or endorsement conflicts. When contradictory
clauses cannot be automatically resolved via strict authority precedence,
it flags the claim for MANUAL_REVIEW and preserves both rule records with citations.
"""

from typing import List, Tuple, Optional, Dict, Any

from app.services.rule_engine.models import (
    RuleCheckResult,
    RuleType,
    RuleStatus,
    ReasonCode,
    RuleEvidence,
)


class ConflictResolver:
    """Detects and arbitrates conflicts between multiple applicable insurance rules."""

    @classmethod
    def detect_and_resolve(
        cls,
        rule_results: List[RuleCheckResult],
        custom_rule_conflicts: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[RuleCheckResult], bool]:
        """
        Scans rule results for logical contradictions.
        Returns (updated_rule_results, has_unresolvable_conflict).
        """
        updated_results = list(rule_results)
        has_conflict = False

        # 1. Process explicit custom conflicts provided in policy endorsement data
        if custom_rule_conflicts:
            for conf in custom_rule_conflicts:
                has_conflict = True
                rule_a = conf.get("ruleA", {})
                rule_b = conf.get("ruleB", {})
                updated_results.append(
                    RuleCheckResult(
                        rule_type=RuleType.OTHER_POLICY_SPECIFIC,
                        rule_id="CONF-ENDORSE-001",
                        condition=conf.get("condition", "GENERAL"),
                        status=RuleStatus.MANUAL_REVIEW,
                        reason_code=ReasonCode.CONFLICTING_RULES,
                        description=(
                            f"Conflicting Policy Rules Detected: '{rule_a.get('description', 'Rule A')}' "
                            f"conflicts with '{rule_b.get('description', 'Rule B')}'. "
                            f"Manual adjudication required."
                        ),
                        evidence=RuleEvidence(
                            source_document=rule_a.get("sourceDocument", "Policy_Contract.pdf"),
                            page=rule_a.get("page", 1),
                            section="Conflict Resolution",
                            text_snippet=f"Rule A: {rule_a.get('text', '')} vs Rule B: {rule_b.get('text', '')}",
                        ),
                        metadata={
                            "ruleA": rule_a,
                            "ruleB": rule_b,
                            "conflictReason": conf.get("reason", "Contradictory endorsement clauses"),
                        },
                    )
                )

        # 2. Scan for internal contradictions across same rule type and condition
        seen_rules: Dict[Tuple[str, str], RuleCheckResult] = {}
        for r in rule_results:
            key = (r.rule_type.value, str(r.condition or "").upper())
            if key in seen_rules:
                prior = seen_rules[key]
                # Check for direct status clash (PASSED vs FAILED with same authority)
                if (
                    r.status != prior.status
                    and {r.status, prior.status} == {RuleStatus.PASSED, RuleStatus.FAILED}
                ):
                    auth_r = (r.evidence.authority_level if r.evidence else "CONTRACTUAL_POLICY_WORDING")
                    auth_prior = (prior.evidence.authority_level if prior.evidence else "CONTRACTUAL_POLICY_WORDING")

                    if auth_r == auth_prior:
                        has_conflict = True
                        updated_results.append(
                            RuleCheckResult(
                                rule_type=r.rule_type,
                                rule_id=f"CONF-{r.rule_type.value[:4]}-001",
                                condition=r.condition,
                                status=RuleStatus.MANUAL_REVIEW,
                                reason_code=ReasonCode.CONFLICTING_RULES,
                                description=(
                                    f"Contradictory Rules for {r.condition}: Clause '{r.rule_id}' states "
                                    f"'{r.status.value}' while clause '{prior.rule_id}' states '{prior.status.value}'. "
                                    f"Manual review required."
                                ),
                                evidence=r.evidence or prior.evidence,
                                metadata={
                                    "conflictingRuleIds": [r.rule_id, prior.rule_id],
                                    "conflictType": "STATUS_CONTRADICTION",
                                },
                            )
                        )
            else:
                seen_rules[key] = r

        return updated_results, has_conflict
