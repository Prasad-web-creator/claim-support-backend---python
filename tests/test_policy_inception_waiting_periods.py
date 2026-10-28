"""
Waiting periods run from the inception date the user declares for a policy (first
start, before any renewals), while policy validity uses the current period.

Scenarios follow the requirement sheet: the same hernia claim against an ICICI
policy held since 2024, an HDFC policy since 2025 and a Star policy since 2026.
"""

import asyncio
from types import SimpleNamespace

from app.services.analysis_interactive_orchestrator import (
    _apply_declared_inception,
    _inception_conflict_question,
    _resolve_prescription_context,
)
from app.services.coverage.business_rules import enforce_business_rules

HERNIA_RX = {
    "visitDate": "2026-10-12",
    "admissionDate": "2026-10-12",
    "diagnosis": "Right inguinal hernia",
    "procedures": [{"name": "Laparoscopic hernia repair"}],
    "hospitalizationRequired": True,
}


def current_policy(start="2026-01-01", end="2026-12-31"):
    return {
        "insuranceCompany": "Insurer",
        "policyStartDate": start,
        "policyEndDate": end,
        "coverageAmount": 1000000,
        "coveredTreatments": ["In-patient hospitalisation"],
    }


def declared(inception, continuous=True, restart=None):
    return SimpleNamespace(inception_date=inception, continuous_renewal=continuous, cover_restart_date=restart)


def evaluate(policy_json, rx=HERNIA_RX):
    result = enforce_business_rules(policy_json, dict(rx))
    return result["deterministicResult"]["status"], " ".join(result.get("blockers") or [])


def test_policy_held_since_2024_covers_hernia():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2024-01-01"))
    status, _ = evaluate(policy)
    assert status == "COVERED"
    assert "first taken on 01-Jan-2024" in policy["waitingPeriodBasis"]


def test_policy_held_since_2025_is_not_yet_covered_and_says_when():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2025-01-01"))
    status, reasons = evaluate(policy)
    assert status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED")
    assert "21 months" in reasons and "Covered from 01-Jan-2027" in reasons


def test_new_policy_from_2026_is_not_covered():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2026-01-01"))
    status, reasons = evaluate(policy)
    assert status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED")
    assert "Covered from 01-Jan-2028" in reasons


def test_break_in_renewal_counts_from_restart():
    policy = current_policy()
    start = _apply_declared_inception(policy, declared("2022-01-01", continuous=False, restart="2025-06-01"))
    assert start.isoformat() == "2025-06-01"
    assert "restarted after a break" in policy["waitingPeriodBasis"]


def test_declared_date_after_current_period_is_not_used():
    policy = current_policy(start="2026-01-01")
    start = _apply_declared_inception(policy, declared("2026-03-01"))
    assert start.isoformat() == "2026-01-01"


def test_validity_still_uses_current_period():
    # An expired current policy is not revived by an old inception date.
    policy = current_policy(start="2025-01-01", end="2025-12-31")
    _apply_declared_inception(policy, declared("2020-01-01"))
    status, _ = evaluate(policy)
    assert status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED")


def test_no_declared_date_keeps_previous_behaviour():
    policy = current_policy()
    assert _apply_declared_inception(policy, declared(None)) is None
    assert "waitingPeriodStartDate" not in policy


def test_document_stating_another_start_asks_the_user():
    policy = current_policy()
    policy["continuity"] = {"continuousCoverageSince": "2023-01-01"}
    start = _apply_declared_inception(policy, declared("2024-01-01"))
    question = _inception_conflict_question(policy, start)
    assert question and question["id"] == "policy_inception_date_conflict"
    assert _inception_conflict_question({"continuity": {"continuousCoverageSince": "2024-01-01"}}, start) is None


def test_outpatient_only_is_not_covered_without_opd_benefit():
    rx = {"visitDate": "2026-10-12", "diagnosis": "Viral fever", "hospitalizationRequired": False}
    policy = current_policy()
    _apply_declared_inception(policy, declared("2024-01-01"))
    status, reasons = evaluate(policy, rx)
    assert status in ("NOT_COVERED", "NOT_CURRENTLY_COVERED")
    assert "Out-patient treatment" in reasons

    policy["coveredTreatments"] = ["In-patient hospitalisation", "OPD cover up to Rs. 5,000"]
    status, _ = evaluate(policy, rx)
    assert status == "COVERED"


def test_short_typed_text_is_treated_as_manual_prescription():
    ctx = asyncio.run(_resolve_prescription_context("user-1", "hernia"))
    assert ctx.is_manual and ctx.manual_text == "hernia"
