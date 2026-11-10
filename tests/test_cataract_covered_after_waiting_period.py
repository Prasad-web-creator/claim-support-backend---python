"""
Regression test — Bug: Incorrect Coverage Decision for Cataract after Waiting Period.

Scenario (from the bug report):
  - Diagnosis:              Cataract
  - Prescription date:      03-Nov-2026
  - First policy inception: 01-Nov-2022
  - Policy validity:        01-Nov-2022 to 31-Mar-2027
  - Policy continuity:      No break
  - Clause:                 Section D.2 – Specified Disease / Procedure Waiting Period (Excl02)
  - Waiting period:         24 months  →  expired on 01-Nov-2024

Root cause that was fixed
-------------------------
The LLM extracts D.2 waiting-period entries such as
  "Cataract and age-related eye ailments (during first 24 months)"
into the policy JSON field ``excludedDiseases``.

``policy_exclusion_entries()`` previously collected ALL entries from
``excludedDiseases`` / ``excludedTreatments`` / ``exclusions`` without
distinguishing permanent exclusions from time-bounded waiting-period entries.
``ExclusionEvaluator._evaluate_policy_exclusions()`` then treated the matched
entry as a permanent exclusion and produced the misleading message
"cataract is permanently excluded … waiting period does not apply".

Fix applied
-----------
``is_waiting_period_entry()`` detects temporal qualifiers in the entry text
("during first 24 months", "waiting period", "first 2 years", etc.) and
``policy_exclusion_entries()`` now skips such entries, leaving them entirely
to the ``WaitingPeriodEvaluator`` which correctly calculates the 24-month
wait from the declared inception date.

Tests in this module assert:
  1. The temporal-qualifier detector correctly identifies waiting-period entries.
  2. Waiting-period entries are excluded from the permanent-exclusion list.
  3. The full engine returns COVERED (waiting period elapsed) — not NOT_COVERED.
  4. No "permanently excluded" blocker appears in the decision.
  5. The WaitingPeriodEvaluator still fires and reports completion.
  6. D.2 entries that name OTHER conditions (hernia, cholelithiasis, etc.) are
     also excluded from permanent exclusions (generic fix, not cataract-specific).
  7. True permanent exclusions that happen to mention a number (e.g. "Excl05")
     are NOT mistakenly filtered out.
  8. Cataract is still correctly blocked when the waiting period is ACTIVE
     (prescription date before 24 months from inception).
"""

import pytest
from app.services.rule_engine.policy_exclusions import (
    is_waiting_period_entry,
    policy_exclusion_entries,
)
from app.services.coverage.business_rules import enforce_business_rules

# ---------------------------------------------------------------------------
# Shared policy fixtures
# ---------------------------------------------------------------------------

# Policy as the LLM would extract it for the bug scenario.
# The D.2 waiting-period entry lands in excludedDiseases because the LLM sees
# it under a section titled "Excluded / Not Covered" — but the parenthetical
# "(during first 24 months)" makes it a temporary restriction, not permanent.
BUG_POLICY = {
    "insuranceCompany": "Demo Health Insurer",
    "policyStartDate": "2022-11-01",
    "policyEndDate": "2027-03-31",
    # Waiting periods run from first inception (set by _apply_declared_inception
    # in the orchestrator; here we pre-populate it as it would be after that step).
    "waitingPeriodStartDate": "2022-11-01",
    "waitingPeriodBasis": "Policy first taken on 01-Nov-2022 (continuous renewal, no break)",
    "waitingPeriodDays": 30,
    "coverageAmount": 500000,
    "coveredTreatments": ["In-patient hospitalisation", "Day care procedures"],
    # This is the problematic entry that the LLM puts into excludedDiseases.
    "excludedDiseases": [
        "Cataract and age-related eye ailments (during first 24 months)",
    ],
    "excludedTreatments": [],
    "exclusions": [],
}

# After 24 months of continuous cover (inception 2022-11-01), the waiting
# period for cataract expired on 2024-11-01.  Prescription date 2026-11-03
# is well past that.
BUG_PRESCRIPTION = {
    "patientName": "Test Patient",
    "visitDate": "2026-11-03",
    "diagnosis": "Cataract",
    "hospitalizationRequired": True,
}


# ---------------------------------------------------------------------------
# 1. is_waiting_period_entry() — temporal-qualifier detection
# ---------------------------------------------------------------------------

class TestIsWaitingPeriodEntry:
    """Unit tests for the temporal-qualifier detector."""

    # --- Should return True (waiting-period entries) ---

    def test_during_first_24_months(self):
        assert is_waiting_period_entry(
            "Cataract and age-related eye ailments (during first 24 months)"
        )

    def test_during_the_first_2_years(self):
        assert is_waiting_period_entry("Hernia (during the first 2 years)")

    def test_first_24_months_bare(self):
        assert is_waiting_period_entry("Gallbladder stones – first 24 months")

    def test_first_2_years_bare(self):
        assert is_waiting_period_entry("Varicose veins first 2 years")

    def test_within_24_months(self):
        assert is_waiting_period_entry("Piles / fistula within 24 months")

    def test_for_the_first_24_months(self):
        assert is_waiting_period_entry("Tonsils for the first 24 months")

    def test_initial_24_months(self):
        assert is_waiting_period_entry("Hydrocele initial 24 months")

    def test_waiting_period_spelled_out(self):
        assert is_waiting_period_entry("Cataract – 24-month waiting period applies")

    def test_excluded_for_months(self):
        assert is_waiting_period_entry("Knee replacement excluded for 24 months")

    def test_excluded_until(self):
        assert is_waiting_period_entry("Joint replacement excluded until completion of 24 months")

    def test_two_year_exclusion(self):
        assert is_waiting_period_entry("Varicose veins two-year exclusion")

    def test_mixed_case(self):
        assert is_waiting_period_entry("CATARACT (During First 24 Months)")

    def test_sinusitis_without_qualifier_is_d2_waiting_period(self):
        # Section D.2 specified disease condition without explicit time qualifier
        assert is_waiting_period_entry("Sinusitis")
        assert is_waiting_period_entry("Deviated Nasal Septum")
        assert is_waiting_period_entry("Benign Prostatic Hypertrophy")
        assert is_waiting_period_entry("Hernia of all types")
        assert is_waiting_period_entry("Calculus diseases of gall bladder, kidney, ureter and urinary bladder")

    # --- Should return False (permanent exclusions) ---

    def test_cosmetic_surgery_permanent(self):
        assert not is_waiting_period_entry("Cosmetic Surgery")

    def test_maternity_permanent(self):
        assert not is_waiting_period_entry("Maternity (except ectopic pregnancy)")

    def test_obesity_permanent(self):
        assert not is_waiting_period_entry("Obesity and Weight Control")

    def test_dental_permanent(self):
        assert not is_waiting_period_entry("Routine Dental / Oral Treatment")

    def test_excl05_number_alone(self):
        # A clause code number should not trigger the filter
        assert not is_waiting_period_entry("Cosmetic Surgery (Excl05)")

    def test_otc_medicines_permanent(self):
        assert not is_waiting_period_entry("OTC medicines and consumables")


# ---------------------------------------------------------------------------
# 2. policy_exclusion_entries() — filter at the collection layer
# ---------------------------------------------------------------------------

class TestPolicyExclusionEntries:
    """Waiting-period entries must not appear in the permanent-exclusion list."""

    def test_cataract_d2_entry_excluded_from_list(self):
        entries = policy_exclusion_entries(BUG_POLICY)
        # The "during first 24 months" entry must be absent
        assert not any("cataract" in e.lower() for e in entries), (
            f"Cataract D.2 entry must not appear in permanent exclusions; got: {entries}"
        )

    def test_permanent_exclusions_still_present(self):
        policy = {
            "excludedDiseases": [
                "Cataract and age-related eye ailments (during first 24 months)",
                "Sinusitis",  # D.2 specified condition without time qualifier
                "Hernia of all types",  # D.2 specified condition
                "Cosmetic Surgery",  # True permanent exclusion
            ],
            "excludedTreatments": ["Maternity (except ectopic pregnancy)"],
        }
        entries = policy_exclusion_entries(policy)
        assert "Cosmetic Surgery" in entries
        assert "Maternity (except ectopic pregnancy)" in entries
        assert not any("cataract" in e.lower() for e in entries)
        assert not any("sinusitis" in e.lower() for e in entries)
        assert not any("hernia" in e.lower() for e in entries)

    def test_all_d2_conditions_filtered(self):
        """Generic fix: ALL waiting-period entries in excludedDiseases are filtered."""
        policy = {
            "excludedDiseases": [
                "Cataract and age-related eye ailments (during first 24 months)",
                "Stones in biliary and urinary systems (first 24 months)",
                "Hernia of all types (first 2 years)",
                "Piles, Fistula and Fissure in-ano – 24-month waiting period",
                "Varicose veins first 24 months",
                # True permanent:
                "Maternity",
                "Cosmetic Surgery",
            ],
        }
        entries = policy_exclusion_entries(policy)
        permanent = {"Maternity", "Cosmetic Surgery"}
        # Only the two true permanents should survive
        assert set(entries) == permanent, (
            f"Expected only {permanent} but got: {entries}"
        )

    def test_empty_policy_returns_empty(self):
        assert policy_exclusion_entries({}) == []

    def test_no_excluded_fields_returns_empty(self):
        assert policy_exclusion_entries({"coverageAmount": 500000}) == []


# ---------------------------------------------------------------------------
# 3. End-to-end engine — cataract COVERED after waiting period elapsed
# ---------------------------------------------------------------------------

class TestCataractCoveredAfterWaitingPeriod:
    """Full engine test for the original bug scenario."""

    def _run(self, policy=None, prescription=None):
        p = policy or BUG_POLICY
        rx = prescription or BUG_PRESCRIPTION
        return enforce_business_rules(p, rx)

    def test_final_status_is_covered(self):
        result = self._run()
        status = result["deterministicResult"]["status"]
        assert status == "COVERED", (
            f"Expected COVERED but got {status}. "
            f"Blockers: {result.get('blockers')}"
        )

    def test_no_permanent_exclusion_blocker(self):
        result = self._run()
        blockers_text = " ".join(result.get("blockers", [])).lower()
        assert "permanently excluded" not in blockers_text, (
            f"'permanently excluded' must not appear when waiting period is complete. "
            f"Blockers: {result.get('blockers')}"
        )

    def test_no_not_covered_reason_code(self):
        result = self._run()
        reason = result["deterministicResult"].get("reasonCode", "")
        assert reason != "PERMANENT_EXCLUSION", (
            f"reasonCode must not be PERMANENT_EXCLUSION; got {reason}"
        )

    def test_waiting_period_completed_rule_present(self):
        """WaitingPeriodEvaluator must report the 24-month wait as PASSED."""
        result = self._run()
        rule_results = result["deterministicResult"].get("ruleResults", [])
        wp_rules = [
            r for r in rule_results
            if r.get("ruleType") == "WAITING_PERIOD"
            and r.get("condition") == "CATARACT"
        ]
        assert wp_rules, "Expected a CATARACT waiting-period rule result"
        assert all(r["status"] == "PASSED" for r in wp_rules), (
            f"CATARACT waiting-period rule must be PASSED; got: {wp_rules}"
        )

    def test_overall_eligible_is_true(self):
        result = self._run()
        assert result["overallEligible"] is True, (
            f"overallEligible must be True; got {result['overallEligible']}. "
            f"Blockers: {result.get('blockers')}"
        )


# ---------------------------------------------------------------------------
# 4. Cataract still BLOCKED when waiting period is ACTIVE
# ---------------------------------------------------------------------------

class TestCataractBlockedDuringWaitingPeriod:
    """Waiting period still applies when the treatment date is within 24 months."""

    def test_cataract_not_covered_within_24_months(self):
        policy = {
            **BUG_POLICY,
            # Inception just 12 months ago — waiting period NOT complete
            "waitingPeriodStartDate": "2025-11-01",
            "waitingPeriodBasis": "Policy first taken on 01-Nov-2025 (continuous renewal, no break)",
            "policyStartDate": "2025-11-01",
            "policyEndDate": "2027-03-31",
        }
        rx = {**BUG_PRESCRIPTION, "visitDate": "2026-11-03"}
        result = enforce_business_rules(policy, rx)
        status = result["deterministicResult"]["status"]
        assert status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED"), (
            f"Expected NOT_COVERED/NOT_CURRENTLY_COVERED for cataract within 24-month "
            f"waiting period; got {status}"
        )

    def test_cataract_blocker_is_waiting_period_not_permanent_exclusion(self):
        policy = {
            **BUG_POLICY,
            "waitingPeriodStartDate": "2025-11-01",
            "waitingPeriodBasis": "Policy first taken on 01-Nov-2025 (continuous renewal, no break)",
            "policyStartDate": "2025-11-01",
            "policyEndDate": "2027-03-31",
        }
        rx = {**BUG_PRESCRIPTION, "visitDate": "2026-11-03"}
        result = enforce_business_rules(policy, rx)
        blockers_text = " ".join(result.get("blockers", [])).lower()
        # The block reason must mention waiting period, not "permanently excluded"
        assert "permanently excluded" not in blockers_text, (
            "When waiting period is active the blocker should cite the waiting "
            "period — not 'permanently excluded'. "
            f"Blockers: {result.get('blockers')}"
        )


# ---------------------------------------------------------------------------
# 5. True permanent exclusions are unaffected
# ---------------------------------------------------------------------------

class TestPermanentExclusionsUnaffected:
    """Ensure permanent exclusions (cosmetic surgery, maternity, etc.) still block."""

    def test_cosmetic_surgery_still_blocked(self):
        policy = {
            **BUG_POLICY,
            "excludedTreatments": ["Cosmetic Surgery", "Rhinoplasty"],
        }
        rx = {**BUG_PRESCRIPTION, "diagnosis": "Rhinoplasty"}
        result = enforce_business_rules(policy, rx)
        status = result["deterministicResult"]["status"]
        assert status == "NOT_COVERED", (
            f"Cosmetic surgery must remain permanently excluded; got {status}"
        )

    def test_maternity_still_blocked(self):
        policy = {
            **BUG_POLICY,
            "excludedTreatments": ["Maternity (except ectopic pregnancy)"],
        }
        rx = {**BUG_PRESCRIPTION, "diagnosis": "Maternity"}
        result = enforce_business_rules(policy, rx)
        status = result["deterministicResult"]["status"]
        assert status == "NOT_COVERED", (
            f"Maternity must remain permanently excluded; got {status}"
        )


# ---------------------------------------------------------------------------
# 6. Sinusitis scenario (from the user's screenshots)
# ---------------------------------------------------------------------------

class TestSinusitisCoveredAfterWaitingPeriod:
    """
    End-to-end test for the exact Sinusitis scenario shown in the user's screenshots:
      - Policy: Prakriti General Insurance Co. Ltd. (PGI/HL/26/4410287)
      - First Inception: 03-Nov-2022
      - Current Period: 01-Apr-2026 to 31-Mar-2027
      - Claim Date: 03-Nov-2026 (48 months continuous cover)
      - Diagnosis: sinusitis (Section D.2 specified disease)
      - Result: COVERED (not permanent exclusion)
    """

    def test_sinusitis_covered_after_48_months(self):
        policy = {
            "insuranceCompany": "Prakriti General Insurance Co. Ltd.",
            "policyNumber": "PGI/HL/26/4410287",
            "policyStartDate": "2026-04-01",
            "policyEndDate": "2027-03-31",
            "waitingPeriodStartDate": "2022-11-03",
            "waitingPeriodBasis": "Policy first taken on 03-Nov-2022 (continuous renewal, no break)",
            "coverageAmount": 1000000,
            "coveredTreatments": ["In-patient hospitalisation"],
            "excludedDiseases": ["Sinusitis"],  # Even if LLM extracted bare "Sinusitis" under excludedDiseases
        }
        rx = {
            "visitDate": "2026-11-03",
            "diagnosis": "sinusitis",
            "hospitalizationRequired": True,
        }
        result = enforce_business_rules(policy, rx)
        status = result["deterministicResult"]["status"]
        assert status == "COVERED", (
            f"Expected COVERED for sinusitis after 48 months; got {status}. "
            f"Blockers: {result.get('blockers')}"
        )
        assert result["overallEligible"] is True
        blockers_text = " ".join(result.get("blockers", [])).lower()
        assert "permanently excluded" not in blockers_text

    def test_sinusitis_blocked_during_waiting_period(self):
        policy = {
            "insuranceCompany": "Prakriti General Insurance Co. Ltd.",
            "policyStartDate": "2026-04-01",
            "policyEndDate": "2027-03-31",
            "waitingPeriodStartDate": "2025-11-03",  # Only 12 months served
            "waitingPeriodBasis": "Policy first taken on 03-Nov-2025",
            "coverageAmount": 1000000,
            "coveredTreatments": ["In-patient hospitalisation"],
            "excludedDiseases": ["Sinusitis"],
        }
        rx = {
            "visitDate": "2026-11-03",
            "diagnosis": "sinusitis",
            "hospitalizationRequired": True,
        }
        result = enforce_business_rules(policy, rx)
        status = result["deterministicResult"]["status"]
        assert status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED")
        blockers_text = " ".join(result.get("blockers", [])).lower()
        assert "permanently excluded" not in blockers_text
        assert "waiting period" in blockers_text

