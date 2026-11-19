"""My Benefits: plain-language view built only from stored policy data."""
from datetime import date
from types import SimpleNamespace

from app.services.benefits.policy_benefits import build_policy_benefits

TODAY = date(2026, 11, 19)


def _policy(pj=None, **kw):
    base = dict(id="p1", policy_name="Health Plus", insurance_company="Acme General", policy_number="ACME/1",
                policy_holder_name="A Person", policy_type="", policy_start_date="2026-04-01",
                policy_end_date="2027-03-31", coverage_amount=0.0, extracted_policy_json=pj or {})
    base.update(kw)
    return SimpleNamespace(**base)


def test_empty_policy_has_no_invented_values():
    r = build_policy_benefits(_policy({}), today=TODAY)
    assert r["hasDetails"] is False
    k = r["keyBenefits"]
    assert k["sumInsured"]["amount"] is None and k["sumInsured"]["available"] is False
    assert k["coPayment"]["available"] is False and k["coPayment"]["value"] is None
    assert k["deductible"]["available"] is False
    assert r["waitingPeriods"]["periods"] == [] and r["roomRent"] is None
    assert r["limits"] == [] and r["additionalBenefits"] == []


def test_validity_status():
    assert build_policy_benefits(_policy(), today=TODAY)["overview"]["status"] == "active"
    assert build_policy_benefits(_policy(policy_end_date="2026-01-01", policy_start_date="2025-01-01"), today=TODAY)["overview"]["status"] == "expired"
    assert build_policy_benefits(_policy(policy_start_date="2027-01-01", policy_end_date="2027-12-31"), today=TODAY)["overview"]["status"] == "upcoming"
    assert build_policy_benefits(_policy(policy_start_date=None, policy_end_date=None), today=TODAY)["overview"]["status"] == "unknown"


def test_conditional_copay_is_not_flattened():
    r = build_policy_benefits(_policy({"coPay": "Nil (20% applies if Insured Person is aged 61 years or above at entry)"}), today=TODAY)
    cp = r["keyBenefits"]["coPayment"]
    assert cp["value"] == "None*"
    assert cp["conditions"] == ["20% applies if Insured Person is aged 61 years or above at entry"]
    assert cp["wording"].startswith("Nil (20%")


def test_copay_without_percentage_says_applies():
    cp = build_policy_benefits(_policy({"coPay": "Applied on the admissible claim amount."}), today=TODAY)["keyBenefits"]["coPayment"]
    assert cp["value"] == "Applies"


def test_plain_copay_percentage():
    cp = build_policy_benefits(_policy({"coPay": "20% of admissible claim amount"}), today=TODAY)["keyBenefits"]["coPayment"]
    assert cp["value"] == "20%"


def test_sum_insured_prefers_document_and_ignores_bogus_record_value():
    assert build_policy_benefits(_policy({"coverageAmount": 1500000}, coverage_amount=0.15), today=TODAY)["keyBenefits"]["sumInsured"]["amount"] == 1500000
    assert build_policy_benefits(_policy({}, coverage_amount=0.15), today=TODAY)["keyBenefits"]["sumInsured"]["amount"] is None
    assert build_policy_benefits(_policy({}, coverage_amount=500000), today=TODAY)["keyBenefits"]["sumInsured"]["amount"] == 500000


def test_waiting_periods_end_dates_only_with_start_date():
    pj = {"waitingPeriodDays": 30,
          "preExistingDiseases": "Any condition diagnosed not more than 48 months prior to the policy. Covered after 36 months of continuous coverage.",
          "coveredDiseases": ["Hernia (after 24 months)", "Cataract"]}
    r = build_policy_benefits(_policy(pj), waiting_start="2025-01-01", waiting_basis="b", today=TODAY)["waitingPeriods"]
    by = {p["key"]: p for p in r["periods"]}
    assert by["initial"]["endDate"] == "2025-01-31" and by["initial"]["status"] == "over"
    assert by["preExisting"]["duration"] == "36 months"  # not the 48-month definition
    assert by["preExisting"]["endDate"] == "2028-01-01" and by["preExisting"]["status"] == "running"
    assert by["specific_24m"]["conditions"] == ["Hernia"]

    no_start = build_policy_benefits(_policy(pj), today=TODAY)["waitingPeriods"]
    assert all(p["endDate"] is None and p["status"] is None for p in no_start["periods"])
    assert no_start["basis"] is None


def test_room_rent_and_condition_sub_limits():
    pj = {"roomEligibility": "Up to 1% of Sum Insured per day (Rs. 10,000 per day)",
          "specialConditions": ["Cataract is limited to INR 40,000 per eye per Policy Period",
                                "Proportionate deduction on associated medical expenses if a higher room category is opted",
                                "No special conditions imposed. Any material misrepresentation shall render the Policy void."]}
    r = build_policy_benefits(_policy(pj), today=TODAY)
    assert r["roomRent"]["value"] == "1% of SI / day"
    assert r["roomRent"]["conditions"] == ["Proportionate deduction on associated medical expenses if a higher room category is opted"]
    cat = next(l for l in r["limits"] if l["title"] == "Cataract")
    assert cat["value"] == "₹40,000" and "per eye" in cat["description"]
    assert r["conditions"]["special"] == ["Any material misrepresentation shall render the Policy void"]


def test_no_room_capping():
    r = build_policy_benefits(_policy({"roomEligibility": "No capping (Room Rent Waiver opted)"}), today=TODAY)
    assert r["roomRent"]["value"] == "No limit"


def test_no_source_reference_is_fabricated():
    r = build_policy_benefits(_policy({"coPay": "10%"}), today=TODAY)
    assert r["keyBenefits"]["coPayment"]["reference"] is None
    r = build_policy_benefits(_policy({"subLimits": [{"name": "Knee replacement", "limit": "Rs. 1,50,000", "page": "12"}]}), today=TODAY)
    sl = r["limits"][0]
    assert sl["title"] == "Knee replacement" and sl["value"] == "₹1,50,000"
    assert sl["reference"] == {"clause": None, "page": "12", "text": None}
