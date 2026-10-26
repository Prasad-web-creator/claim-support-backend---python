"""
"How This Cover Compares": modern treatment and pre/post-hospitalisation rows
are read from the policy, never assumed.
"""

import pytest

from app.services.coverage.reference_benchmark_service import generate_reference_comparison


def rows(policy_json):
    result = generate_reference_comparison(policy_json, {"diagnosis": "Appendicitis"})
    return {r["feature"]: r for r in result["featureComparisons"]}


@pytest.mark.parametrize("policy, modern, window", [
    ({"preHospitalizationDays": 60, "postHospitalizationDays": 90, "modernTreatmentsLimit": "Up to 50% of Sum Insured"},
     ("Up to 50% of Sum Insured", "May cost you extra"), ("60 Days Pre / 90 Days Post", "May cost you extra")),
    ({"preHospitalizationDays": 60, "postHospitalizationDays": 180, "modernTreatmentsLimit": "Up to Sum Insured"},
     ("Up to Sum Insured", "Same as best plan"), ("60 Days Pre / 180 Days Post", "Same as best plan")),
    # Policies extracted before the dedicated fields existed: read from the wording.
    ({"coveredTreatments": ["Pre-hospitalisation: 60 days prior to admission", "Post-hospitalisation: 90 days after discharge"],
      "specialConditions": ["Modern Treatment Methods: Up to 50% of Sum Insured"]},
     ("Up to 50% of Sum Insured", "May cost you extra"), ("60 Days Pre / 90 Days Post", "May cost you extra")),
    ({"coverageAmount": 500000},
     ("Not stated in the policy", "Not stated in policy"), ("Not stated in the policy", "Not stated in policy")),
])
def test_rows_reflect_the_policy(policy, modern, window):
    r = rows(policy)
    m, w = r["Modern Treatments & Robotic Surgery"], r["Pre & Post Hospitalization Coverage"]
    assert (m["userPolicy"], m["comparisonStatus"]) == modern
    assert (w["userPolicy"], w["comparisonStatus"]) == window


def test_every_row_uses_a_plain_level_label():
    labels = {"Same as best plan", "Best plan offers more", "May cost you extra", "Not covered - you pay", "Not stated in policy"}
    r = rows({"coverageAmount": 1000000, "roomEligibility": "Up to 1% of Sum Insured per day"})
    assert {row["comparisonStatus"] for row in r.values()} <= labels
    assert r["Base Sum Insured"]["comparisonStatus"] == "Best plan offers more"
    assert r["Room Rent Category"]["comparisonStatus"] == "May cost you extra"
    assert r["Consumables & Non-Payables"]["comparisonStatus"] == "Not covered - you pay"
