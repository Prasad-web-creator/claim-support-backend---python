"""
A treatment the policy itself excludes (e.g. Maternity under D.18 / Excl18) is
decided by the AI from the policy's own wording, including its exceptions
(ectopic pregnancy). The app checks never block it by matching words.
"""

from app.services.coverage.business_rules import enforce_business_rules

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


def test_maternity_is_decided_by_the_ai_not_the_app_checks():
    """The policy's maternity exclusion (and its ectopic exception) is read by the AI, not matched by words."""
    br = enforce_business_rules(POLICY, manual_rx("Maternity"), policy_text=POLICY_TEXT)
    assert br["blockers"] == []
    assert br["deterministicResult"]["status"] != "NOT_COVERED"
    assert "permanently excluded" not in str(br).lower()


def test_no_listed_exclusion_blocks_without_policy_text_either():
    br = enforce_business_rules(POLICY, manual_rx("Maternity"), policy_text=None)
    assert br["blockers"] == []
