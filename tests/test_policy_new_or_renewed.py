"""
The upload step asks: start date → new or renewed → (renewed) renewed every year without
a break. After a break, cover is counted from the uploaded policy's start date. These answers decide
where waiting periods count from and what the Policy Continuity card shows.
"""

from types import SimpleNamespace

from app.services.analysis_interactive_orchestrator import _apply_confirmed_renewal, _apply_declared_inception
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


def test_break_counts_from_the_uploaded_policy_start():
    policy = current_policy()
    start = _apply_declared_inception(policy, declared("2020-01-01", is_renewal=True, continuous=False))
    assert start.isoformat() == "2026-10-01"
    assert "coverage gap in your policy continuity" in policy["waitingPeriodBasis"]

    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Coverage Gap Detected"
    assert "You have a coverage gap in your policy continuity" in result["summary"]
    assert result["breakStartDate"] is None
    assert result["coverRestartDate"] is None
    assert result["coverageGaps"] == []


def test_break_ignores_stale_break_dates_from_older_app_versions():
    policy = current_policy()
    start = _apply_declared_inception(
        policy, declared("2020-01-01", is_renewal=True, continuous=False, break_start="2023-01-01", restart="2023-03-01")
    )
    assert start.isoformat() == "2026-10-01"
    result = declared_policy_continuity(policy, RX)
    assert result["coverRestartDate"] is None


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


# ── Selected New, then confirmed Renewed in the clarification ─────────────────

async def _confirm_renewal(policy_json):
    session = SimpleNamespace(policy_json=policy_json, policy_id=None)
    await _apply_confirmed_renewal(session)
    return session.policy_json


async def test_confirmed_renewal_clears_a_stale_break_answer():
    # A "No, there was a break" answer left on the record from an earlier upload is
    # ignored while the policy is New, and must not come back once it is confirmed Renewed.
    policy = current_policy(start="2026-01-15", end="2027-01-14")
    _apply_declared_inception(policy, declared("2026-01-15", is_renewal=False, continuous=False))
    assert declared_policy_continuity(policy, {"admissionDate": "2026-10-12"})["status"] == "Continuity Verified"

    policy = await _confirm_renewal(policy)
    assert policy["continuousRenewal"] is None
    assert policy["waitingPeriodStartDate"] == "2026-01-15"

    result = declared_policy_continuity(policy, {"admissionDate": "2026-10-12"})
    assert result["status"] == "Continuity Verified"
    assert result["isRenewal"] is True
    assert "renewed policy" in result["summary"]
    assert result["coverageGaps"] == []


async def test_confirmed_renewal_with_a_document_first_start_date():
    policy = current_policy(start="2026-01-15", end="2027-01-14")
    policy["continuity"] = {
        "continuousCoverageSince": "2021-01-15",
        "policyStartDate": "2026-01-15",
        "continuityEvidence": ["Continuous coverage since 15/01/2021"],
    }
    _apply_declared_inception(policy, declared("2026-01-15", is_renewal=False, continuous=False))
    policy = await _confirm_renewal(policy)
    assert policy["waitingPeriodStartDate"] == "2021-01-15"
    result = declared_policy_continuity(policy, {"admissionDate": "2026-10-12"})
    assert result["status"] == "Continuity Verified"
    assert result["originalPolicyStartDate"] == "2021-01-15"


def test_record_reread_after_confirmation_shows_no_gap():
    # The report screen re-applies the Policy record's answers. Even a record still
    # holding the stale break answer (reports made before this fix) shows no gap when
    # the first start is the current period's start: there is no earlier period to break.
    policy = current_policy(start="2026-01-15", end="2027-01-14")
    policy.update(declared_policy_inputs(declared("2026-01-15", is_renewal=True, continuous=False)))
    result = declared_policy_continuity(policy, {"admissionDate": "2026-10-12"})
    assert result["status"] == "Continuity Verified"
    assert result["coverageGaps"] == []


# ── Break answers against the dates ───────────────────────────────────────────

def test_break_without_any_earlier_period_is_not_a_gap():
    policy = current_policy()
    start = _apply_declared_inception(policy, declared("2026-10-01", is_renewal=True, continuous=False))
    assert start.isoformat() == "2026-10-01"
    assert "coverage gap" not in policy["waitingPeriodBasis"]
    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Continuity Verified"


def test_first_start_after_the_current_period_is_counted_from_the_period_start():
    # A first-start date later than the current period start is a typing slip: cover
    # is counted from the period start, and a claim inside the period is not rejected.
    policy = current_policy()
    _apply_declared_inception(policy, declared("2026-10-20", is_renewal=True, continuous=False))
    result = declared_policy_continuity(policy, {"visitDate": "2026-10-10"})
    assert result["status"] == "Continuity Verified"
    assert result["originalPolicyStartDate"] == "2026-10-01"


def test_break_with_claim_before_the_uploaded_policy_starts():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2020-01-01", is_renewal=True, continuous=False))
    result = declared_policy_continuity(policy, {"visitDate": "2026-09-15"})
    assert result["status"] == "Coverage Gap Detected"
    assert "does not cover that date" in result["summary"]


def test_renewed_without_break_claim_in_an_earlier_period():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2022-10-01", is_renewal=True, continuous=True))
    result = declared_policy_continuity(policy, {"visitDate": "2026-09-15"})
    assert result["status"] == "Continuity Verified"


def test_claim_after_the_policy_ends_is_a_gap():
    policy = current_policy()
    _apply_declared_inception(policy, declared("2022-10-01", is_renewal=True, continuous=True))
    result = declared_policy_continuity(policy, {"visitDate": "2027-10-15"})
    assert result["status"] == "Coverage Gap Detected"
    assert result["coverageGaps"][0]["from"] == "2027-10-01"


def test_unreadable_answers_are_never_read_as_a_gap():
    policy = current_policy()
    policy.update({"declaredInceptionDate": "2022-10-01", "isRenewal": True, "continuousRenewal": ""})
    assert declared_policy_continuity(policy, RX)["status"] == "Continuity Verified"

    policy.update({"isRenewal": "", "continuousRenewal": None})
    result = declared_policy_continuity(policy, RX)
    assert result["status"] == "Continuity Verified"
    assert "new policy" not in result["summary"]


def test_string_answers_are_understood():
    policy = current_policy()
    policy.update({"declaredInceptionDate": "2022-10-01", "isRenewal": "true", "continuousRenewal": "false"})
    assert declared_policy_continuity(policy, RX)["status"] == "Coverage Gap Detected"


def test_datetime_answers_from_the_record_are_understood():
    from datetime import datetime
    policy = current_policy(start="2026-01-15", end="2027-01-14")
    policy.update(declared_policy_inputs(declared(datetime(2026, 1, 15), is_renewal=True, continuous=True)))
    result = declared_policy_continuity(policy, {"admissionDate": "2026-10-12"})
    assert result["status"] == "Continuity Verified"
    assert result["originalPolicyStartDate"] == "2026-01-15"
