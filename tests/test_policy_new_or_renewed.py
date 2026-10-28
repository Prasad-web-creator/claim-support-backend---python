"""
The upload step asks: start date → new or renewed → (renewed) renewed every year without
a break → (break) date the break started → date cover restarted. These answers decide
where waiting periods count from and what the Policy Continuity card shows.
"""

from types import SimpleNamespace

from app.services.analysis_interactive_orchestrator import _apply_declared_inception
from app.services.coverage.policy_continuity import declared_policy_continuity, declared_policy_inputs

RX = {"visitDate": "2026-10-27"}


def current_policy(start="2026-10-01", end="2027-09-30"):
    return {"insuranceCompany": "Insurer", "policyNumber": "P1", "policyStartDate": start, "policyEndDate": end}


def declared(inception, is_renewal=None, continuous=None, break_start=None, restart=None):
    return SimpleNamespace(
        inception_date=inception,
        is_renewal=is_renewal,
        continuous_renewal=continuous,
        break_start_date=break_start,
        cover_restart_date=restart,
    )


def test_new_policy_counts_from_its_start_and_says_so():
    policy = current_policy()
    start = _apply_declared_inception(policy, declared("2026-10-01", is_renewal=False))
    assert start.isoformat() == "2026-10-01"
    assert "new policy first taken on 01-Oct-2026" in policy["waitingPeriodBasis"]

    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Continuity Verified"
    assert result["isRenewal"] is False
    assert "new policy" in result["summary"]
    assert result["coverageGaps"] == []


def test_renewed_without_break_counts_from_first_start():
    policy = current_policy()
    start = _apply_declared_inception(policy, declared("2022-10-01", is_renewal=True, continuous=True))
    assert start.isoformat() == "2022-10-01"
    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Continuity Verified"
    assert result["isRenewal"] is True


def test_break_shows_the_gap_from_break_start_to_restart():
    policy = current_policy()
    start = _apply_declared_inception(
        policy, declared("2020-01-01", is_renewal=True, continuous=False, break_start="2023-01-01", restart="2023-03-01")
    )
    assert start.isoformat() == "2023-03-01"
    assert policy["breakStartDate"] == "2023-01-01"

    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Coverage Gap Detected"
    assert result["breakStartDate"] == "2023-01-01"
    assert result["coverRestartDate"] == "2023-03-01"
    gap = result["coverageGaps"][0]
    assert (gap["from"], gap["to"], gap["days"]) == ("2023-01-01", "2023-02-28", 59)


def test_new_policy_ignores_stale_break_answers():
    policy = current_policy()
    policy.update(declared_policy_inputs(
        declared("2026-10-01", is_renewal=False, continuous=False, break_start="2025-01-01", restart="2025-02-01")
    ))
    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Continuity Verified"
    assert result["coverageGaps"] == []


def test_older_policies_without_the_answer_are_unchanged():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2022-10-01", continuous=True))
    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Continuity Verified"
    assert result["isRenewal"] is None
