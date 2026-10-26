"""
Dates as printed on real documents, and undecided analyses.

A prescription dated "12-Oct-2026, 09:40 AM" used to be unreadable, which sent
covered claims to manual review; the report then showed manual review as
"Not Covered".
"""

from datetime import date

import pytest

from app.services.coverage.business_rules import enforce_business_rules
from app.services.coverage.report_generator import generate_report
from app.services.rule_engine.date_utils import first_parsed_date, parse_flexible_date


@pytest.mark.parametrize("text, expected", [
    ("12-Oct-2026, 09:40 AM", date(2026, 10, 12)),
    ("01-Apr-2026 00:00 Hrs", date(2026, 4, 1)),
    ("31-Mar-2027 Midnight", date(2027, 3, 31)),
    ("01 April 2026", date(2026, 4, 1)),
    ("1st April 2026", date(2026, 4, 1)),
    ("April 1, 2026", date(2026, 4, 1)),
    ("2026-04-01T00:00:00", date(2026, 4, 1)),
    ("12/10/2026", date(2026, 10, 12)),
    ("not a date", None),
])
def test_document_date_formats(text, expected):
    assert parse_flexible_date(text) == expected


def test_unreadable_field_does_not_hide_a_readable_one():
    assert first_parsed_date("as advised", None, "2026-10-12") == date(2026, 10, 12)


POLICY = {
    "insuranceCompany": "Insurer A",
    "policyStartDate": "01-Apr-2026 00:00 Hrs",
    "policyEndDate": "31-Mar-2027 Midnight",
    "coverageAmount": 1000000,
    "coveredTreatments": ["In-patient hospitalisation"],
}
RX = {
    "visitDate": "12-Oct-2026, 09:40 AM",
    "diagnosis": "Acute Appendicitis",
    "hospitalizationRequired": True,
    "medicalTests": [{"name": "Complete Blood Count"}],
}


def test_dated_prescription_is_evaluated_not_referred():
    result = enforce_business_rules(dict(POLICY), dict(RX))
    assert result["deterministicResult"]["status"] == "COVERED"


def test_undecided_analysis_is_manual_review_not_rejection():
    policy = {k: v for k, v in POLICY.items() if k != "policyStartDate"}
    rules = enforce_business_rules(policy, dict(RX))
    assert rules["deterministicResult"]["status"] == "MANUAL_REVIEW"

    report = generate_report(
        policy_json=policy,
        prescription_json=dict(RX),
        business_rule_results=rules,
        coverage_analysis={"comparison": []},
        processing_time_ms=1,
    )

    assert report["overallStatus"] == "Manual Review Required"
    assert report["comparison"]
    assert all(item["coverageStatus"] == "Manual Review" for item in report["comparison"])


# ─── Clarification answers and the policy start date conflict check ──────────

@pytest.mark.parametrize("answer, expected", [
    ("14-Oct-2026", date(2026, 10, 14)),  # date picked in the app's calendar
    ("Use my entered date (01-03-2026)", date(2026, 3, 1)),
    ("Use policy document date (01-04-2026)", date(2026, 4, 1)),
    ("2026-05-20", date(2026, 5, 20)),
    ("Not sure", None),
])
def test_dates_are_read_from_clarification_answers(answer, expected):
    from app.services.analysis_interactive_orchestrator import _date_from_answer
    parsed = _date_from_answer(answer)
    assert (parsed.date() if parsed else None) == expected


@pytest.mark.parametrize("printed", ["01-Apr-2026 00:00 Hrs", "April 1, 2026", "1st April 2026"])
def test_document_start_date_is_readable_for_the_conflict_check(printed):
    # An unreadable document date used to skip the conflict question and keep the user's date.
    from app.services.analysis_interactive_orchestrator import _parse_and_format_date
    parsed, _ = _parse_and_format_date(printed)
    assert parsed is not None and parsed.date() == date(2026, 4, 1)
