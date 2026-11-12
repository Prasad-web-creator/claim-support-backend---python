"""
Regression test — Cataract after its waiting period (bug report scenario).

  - Diagnosis:              Cataract
  - Prescription date:      03-Nov-2026
  - First policy inception: 01-Nov-2022 (no break)
  - Policy validity:        01-Nov-2022 to 31-Mar-2027

Which waiting period applies is decided by the AI from the policy's own wording.
These tests pin what the app itself does: it never blocks a treatment on a
built-in list, it still blocks a treatment outside the policy period, and it
gives the AI the exact cover duration.
"""

import pytest
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

    def test_cover_duration_given_to_the_ai(self):
        """Which waiting period applies is decided by the AI; the app gives it the exact cover duration."""
        result = self._run()
        facts = result["coverDuration"]
        assert facts["completedMonths"] == 48
        assert facts["coverStartDate"] == "2022-11-01"

    def test_overall_eligible_is_true(self):
        result = self._run()
        assert result["overallEligible"] is True, (
            f"overallEligible must be True; got {result['overallEligible']}. "
            f"Blockers: {result.get('blockers')}"
        )


# ---------------------------------------------------------------------------
# 4. Waiting periods are decided by the AI from the policy's own list
# ---------------------------------------------------------------------------

class TestWaitingPeriodDecidedByTheAI:
    """
    The app no longer applies a built-in waiting-period list: the AI decides from
    this policy's own waiting periods and exceptions, using the exact cover
    duration the app computes.
    """

    def _within_24_months(self):
        policy = {
            **BUG_POLICY,
            "waitingPeriodStartDate": "2025-11-01",
            "waitingPeriodBasis": "Policy first taken on 01-Nov-2025 (continuous renewal, no break)",
            "policyStartDate": "2025-11-01",
            "policyEndDate": "2027-03-31",
        }
        rx = {**BUG_PRESCRIPTION, "visitDate": "2026-11-03"}
        return enforce_business_rules(policy, rx)

    def test_app_checks_do_not_block_on_a_builtin_list(self):
        result = self._within_24_months()
        assert result["blockers"] == []
        assert result["deterministicResult"]["status"] == "COVERED"

    def test_exact_cover_duration_is_given(self):
        facts = self._within_24_months()["coverDuration"]
        assert facts["completedMonths"] == 12
        assert facts["daysActive"] == 367


# ---------------------------------------------------------------------------
# 5. Exclusions are decided by the AI from the policy's own wording
# ---------------------------------------------------------------------------

class TestExclusionsDecidedByTheAI:
    """No built-in exclusion list or word matching blocks a treatment."""

    def test_cosmetic_surgery_is_not_blocked_by_the_app(self):
        policy = {**BUG_POLICY, "excludedTreatments": ["Cosmetic Surgery", "Rhinoplasty"]}
        rx = {**BUG_PRESCRIPTION, "diagnosis": "Rhinoplasty"}
        result = enforce_business_rules(policy, rx)
        assert result["blockers"] == []
        assert result["deterministicResult"]["status"] != "NOT_COVERED"

    def test_bariatric_surgery_is_not_blocked_by_the_app(self):
        rx = {**BUG_PRESCRIPTION, "diagnosis": "Bariatric surgery"}
        result = enforce_business_rules(BUG_POLICY, rx)
        assert result["blockers"] == []
        text = str(result).lower()
        assert "statutory" not in text and "permanently excluded" not in text

    def test_maternity_is_not_blocked_by_the_app(self):
        policy = {**BUG_POLICY, "excludedTreatments": ["Maternity (except ectopic pregnancy)"]}
        rx = {**BUG_PRESCRIPTION, "diagnosis": "Ectopic pregnancy"}
        result = enforce_business_rules(policy, rx)
        assert result["blockers"] == []


# ---------------------------------------------------------------------------
# 6. Policy period checks still apply
# ---------------------------------------------------------------------------

class TestPolicyPeriodStillChecked:
    def test_treatment_after_policy_expiry_is_blocked(self):
        rx = {**BUG_PRESCRIPTION, "visitDate": "2027-06-01"}
        result = enforce_business_rules(BUG_POLICY, rx)
        assert result["overallEligible"] is False
        assert any("expir" in b.lower() for b in result["blockers"])
