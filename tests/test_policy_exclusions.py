"""
A treatment the policy itself excludes (e.g. Maternity under D.18 / Excl18) is
reported as a permanent policy exclusion, quoting the policy's own clause, rather
than as the initial 30-day waiting period.
"""

from app.services.coverage.business_rules import enforce_business_rules
from app.services.coverage.coverage_analysis import _format_item_card
from app.services.rule_engine.policy_exclusions import (
    find_exclusion_clause,
    find_schedule_not_covered,
    match_policy_exclusion,
)

POLICY_TEXT = """Waiting Periods
Initial waiting period (all illnesses except accidents) - Code Excl03
30 days from first policy inception
Maternity / Newborn
Not covered under this plan
Special Conditions / Endorsements: No special conditions imposed.
D.17 Sterility and Infertility (Excl17). Expenses related to sterility and infertility, including contraception, assisted reproduction
services, gestational surrogacy and reversal of sterilisation.
D.18 Maternity (Excl18). Medical treatment expenses traceable to childbirth (including complicated deliveries and caesarean sections
incurred during hospitalisation) except ectopic pregnancy, and expenses towards miscarriage or termination of pregnancy.
D.19 Other Exclusions. War, invasion, acts of foreign enemies; out-patient treatment.
"""

POLICY = {
    "policyStartDate": "01-Oct-2026",
    "policyEndDate": "30-Sep-2027",
    "coverageAmount": 1500000,
    "waitingPeriodDays": 30,
    "excludedDiseases": ["Hernia of all types, Hydrocele"],
    "excludedTreatments": ["OPD", "Maternity (except ectopic pregnancy)", "Sterility and Infertility", "Maternity / Newborn"],
}


def manual_rx(text):
    return {
        "visitDate": "2026-10-27",
        "diagnosis": text,
        "manualText": text,
        "isManual": True,
        "prescriptionSource": "Self-entered Prescription",
    }


def test_matches_excluded_treatment_but_not_its_stated_exception():
    entries = POLICY["excludedTreatments"]
    assert match_policy_exclusion(["Maternity"], entries)["entry"] == "Maternity (except ectopic pregnancy)"
    assert match_policy_exclusion(["Ectopic pregnancy"], entries) is None
    assert match_policy_exclusion(["Fever"], entries) is None


def test_finds_clause_and_schedule_row():
    clause = find_exclusion_clause(POLICY_TEXT, "maternity")
    assert (clause["section"], clause["title"], clause["code"]) == ("D.18", "Maternity (Excl18)", "Excl18")
    assert clause["wording"].startswith("Medical treatment expenses traceable to childbirth")
    assert find_schedule_not_covered(POLICY_TEXT, "maternity") == "Maternity / Newborn: Not covered under this plan"


def test_maternity_reason_cites_policy_exclusion_not_waiting_period():
    rx = manual_rx("Maternity")
    br = enforce_business_rules(POLICY, rx, policy_text=POLICY_TEXT)
    assert br["deterministicResult"]["status"] == "NOT_COVERED"

    card = _format_item_card(
        {"item": "Maternity"}, POLICY, rx,
        override_status="Not Covered",
        reason_code=br["deterministicResult"]["reasonCode"],
        blockers=br["blockers"],
    )
    reason = card["coverageStatusReason"]
    assert "D.18 Maternity (Excl18)" in reason
    assert "Maternity / Newborn: Not covered under this plan" in reason
    assert "30-day" not in reason
    assert "traceable to childbirth" in card["policyEvidence"]
    assert "permanently excluded" in card["financialDecision"]


def test_excluded_without_policy_text_names_the_listed_exclusion():
    br = enforce_business_rules(POLICY, manual_rx("Maternity"), policy_text=None)
    assert any('"Maternity (except ectopic pregnancy)"' in b for b in br["blockers"])
    assert len([b for b in br["blockers"] if "excluded" in b.lower() and "maternity" in b.lower()]) == 1
