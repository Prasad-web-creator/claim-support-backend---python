"""
Network, Geography & Hospitalization Requirements Evaluator.
Evaluates hospital network empanelment, geographic territorial jurisdiction (domestic vs international),
and minimum duration of hospital stay (24h standard vs 2+ hours day care).
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


class NetworkGeographyEvaluator:
    """Evaluates network compliance, territorial validity, and hospitalization duration."""

    @classmethod
    def evaluate(
        cls,
        policy_data: Dict[str, Any],
        prescription_data: Dict[str, Any],
        normalized_conditions: List[NormalizedCondition],
    ) -> List[RuleCheckResult]:
        results: List[RuleCheckResult] = []

        # 1. Geographic Territorial Restriction Evaluation
        treatment_country = (
            prescription_data.get("treatmentCountry")
            or prescription_data.get("country")
            or ""
        ).strip().lower()

        worldwide_covered = bool(
            policy_data.get("worldwideCovered")
            or policy_data.get("internationalEmergency")
            or "worldwide" in (policy_data.get("geographicalLimits") or "").lower()
            or "global" in (policy_data.get("geographicalLimits") or "").lower()
        )

        if treatment_country and treatment_country not in ("india", "in", "domestic", ""):
            if not worldwide_covered:
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.GEOGRAPHICAL,
                        rule_id="GEO-TERRITORY-001",
                        status=RuleStatus.FAILED,
                        reason_code=ReasonCode.GEOGRAPHIC_RESTRICTION,
                        description=(
                            f"Geographical Restriction: Medical treatment occurred in '{treatment_country.title()}', "
                            f"which is outside the policy's territorial jurisdiction (India only). "
                            f"Worldwide emergency rider is not attached."
                        ),
                        evidence=RuleEvidence(
                            source_document="Policy_Terms.pdf",
                            page=1,
                            section="Territorial Scope",
                            text_snippet="Coverage is limited to hospitalizations within the Republic of India.",
                            authority_level="CONTRACTUAL_POLICY_WORDING",
                        ),
                        metadata={"treatmentCountry": treatment_country, "worldwideCovered": False},
                    )
                )

        # 2. Network Hospital / Provider Requirement Evaluation
        is_network = prescription_data.get("isNetworkHospital")
        hospital_name = (
            prescription_data.get("hospitalName")
            or prescription_data.get("providerName")
            or ""
        ).strip()
        network_strict = bool(
            policy_data.get("cashlessOnly")
            or policy_data.get("networkOnly")
            or "network only" in str(policy_data.get("hospitalNetwork", "")).lower()
        )

        if is_network is False and network_strict:
            results.append(
                RuleCheckResult(
                    rule_type=RuleType.NETWORK,
                    rule_id="NET-PROVIDER-001",
                    status=RuleStatus.MANUAL_REVIEW,
                    reason_code=ReasonCode.NETWORK_RESTRICTION,
                    description=(
                        f"Network Restriction: Treatment was received at non-network facility '{hospital_name or 'Unknown'}'. "
                        f"Policy requires network admission for cashless settlement or reimbursement pre-authorization."
                    ),
                    evidence=RuleEvidence(
                        source_document="Policy_Terms.pdf",
                        page=1,
                        section="Network Hospitalization Requirement",
                        text_snippet="Treatment must be taken in Network Hospitals for cashless claims.",
                    ),
                    metadata={"hospitalName": hospital_name, "isNetwork": False},
                )
            )

        # 3. Hospitalization Duration Requirement Evaluation
        # General: 24+ hours; Day Care / Modern Treatments: 2+ hours
        hosp_hours = prescription_data.get("hospitalizationHours") or prescription_data.get("durationHours")
        hosp_required = prescription_data.get("hospitalizationRequired")

        is_day_care = any(
            n.category in ("OPHTHALMIC", "SURGICAL", "MODERN_TREATMENT")
            or n.normalized_value in ("CATARACT", "CHOLELITHIASIS", "HERNIA", "ROBOTIC_SURGERY")
            for n in normalized_conditions
        )

        if hosp_hours is not None:
            try:
                hours = float(hosp_hours)
                min_required = 2.0 if is_day_care else 24.0

                if hours < min_required and hosp_required is not False:
                    results.append(
                        RuleCheckResult(
                            rule_type=RuleType.HOSPITALIZATION,
                            rule_id="HOSP-DURATION-001",
                            status=RuleStatus.FAILED,
                            reason_code=ReasonCode.HOSPITALIZATION_CRITERIA_NOT_MET,
                            description=(
                                f"Hospitalization Criteria Not Met: Stay duration of {hours:.1f} hours is below "
                                f"the required minimum of {min_required:.0f} hours "
                                f"({'Day Care 2+ hours' if is_day_care else 'Inpatient 24+ hours'})."
                            ),
                            evidence=RuleEvidence(
                                source_document="Mail - ClaimSupport 2.pdf",
                                page=1,
                                section="Hospitalization Stay Guidelines",
                                text_snippet="Covered for hospital stays lasting 2+ hours (AYUSH 24+ hours).",
                            ),
                            metadata={"actualHours": hours, "minRequiredHours": min_required},
                        )
                    )
            except Exception:
                pass

        return results
