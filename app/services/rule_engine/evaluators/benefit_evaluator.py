"""
Benefit Evaluator.
Evaluates whether claimed procedures and benefits (e.g. Air Ambulance, Road Ambulance,
AYUSH, Domiciliary, Day Care, OPD) are covered under the policy plan and variant tier.
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


class BenefitEvaluator:
    """Evaluates benefit inclusions and variant-level tier exclusions."""

    # ReAssure 3.0 Variant Specifications (from Leaflet artwork.pdf & Mail - ClaimSupport 2.pdf)
    VARIANT_BENEFITS = {
        "classic": {
            "roadAmbulance": "INR 2,000",
            "airAmbulanceCovered": False,
            "airAmbulance": "Not Covered (NA)",
            "modernTreatments": "Up to INR 1 Lac sub-limit",
            "roomCategory": "General room",
        },
        "select": {
            "roadAmbulance": "INR 2,000",
            "airAmbulanceCovered": False,
            "airAmbulance": "Not Covered (NA)",
            "modernTreatments": "Up to full Sum Insured",
            "roomCategory": "Single private room",
        },
        "elite": {
            "roadAmbulance": "Up to Sum Insured",
            "airAmbulanceCovered": True,
            "airAmbulance": "Up to INR 2.5 Lacs",
            "modernTreatments": "Up to full Sum Insured",
            "roomCategory": "Single private room (upgradable)",
        },
        "black": {
            "roadAmbulance": "Up to Sum Insured",
            "airAmbulanceCovered": True,
            "airAmbulance": "Up to Sum Insured",
            "modernTreatments": "Up to full Sum Insured",
            "roomCategory": "Any room category",
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

        variant = (
            policy_data.get("variant")
            or policy_data.get("policyType")
            or policy_data.get("planTier")
            or "All"
        ).strip().lower()

        # 1. Air Ambulance Benefit Evaluation
        ambulance_claimed = (
            prescription_data.get("airAmbulanceClaimed") is True
            or "air ambulance" in (prescription_data.get("manualText") or "").lower()
            or any("air ambulance" in str(p).lower() for p in (prescription_data.get("procedures") or []))
        )

        if ambulance_claimed:
            variant_cfg = cls.VARIANT_BENEFITS.get(variant, cls.VARIANT_BENEFITS["classic"])
            is_covered = variant_cfg.get("airAmbulanceCovered", False)

            if not is_covered:
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.BENEFIT,
                        rule_id="BEN-AMB-AIR-001",
                        condition="AIR_AMBULANCE",
                        status=RuleStatus.FAILED,
                        reason_code=ReasonCode.BENEFIT_NOT_INCLUDED,
                        description=(
                            f"Benefit Unavailable: Air Ambulance is not covered under plan variant '{variant.capitalize()}'. "
                            f"Only available in Elite (INR 2.5L) or Black tiers."
                        ),
                        evidence=RuleEvidence(
                            source_document="Leaflet artwork.pdf",
                            page=1,
                            section="Variant Comparison Table — Ambulance",
                            clause_id="BEN-AMBULANCE",
                            text_snippet=f"Air Ambulance: Classic = NA, Select = NA, Elite = 2.5 Lac, Black = Up to SI.",
                            authority_level="REFERENCE_BENCHMARK",
                        ),
                        metadata={"variant": variant, "benefit": "Air Ambulance"},
                    )
                )
            else:
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.BENEFIT,
                        rule_id="BEN-AMB-AIR-001",
                        condition="AIR_AMBULANCE",
                        status=RuleStatus.PASSED,
                        reason_code=ReasonCode.BENEFIT_COVERED,
                        description=f"Air Ambulance is covered under plan variant '{variant.capitalize()}' ({variant_cfg.get('airAmbulance')}).",
                        evidence=RuleEvidence(
                            source_document="Leaflet artwork.pdf",
                            page=1,
                            section="Variant Comparison Table — Ambulance",
                        ),
                        metadata={"variant": variant, "benefit": "Air Ambulance"},
                    )
                )

        # 2. Modern Treatments Coverage Benefit Check
        for norm in normalized_conditions:
            if norm.normalized_value == "ROBOTIC_SURGERY":
                results.append(
                    RuleCheckResult(
                        rule_type=RuleType.BENEFIT,
                        rule_id="BEN-MODERN-001",
                        condition="ROBOTIC_SURGERY",
                        status=RuleStatus.PASSED,
                        reason_code=ReasonCode.BENEFIT_COVERED,
                        description="Modern Treatments (including Robotic Surgery) are covered subject to applicable tier limits.",
                        evidence=RuleEvidence(
                            source_document="Policy_Document.pdf",
                            page=1,
                            section="Modern Treatments Coverage",
                            text_snippet="Modern treatments including robotic surgeries and stem cell therapy are eligible benefits.",
                        ),
                        metadata={"variant": variant},
                    )
                )

        return results
