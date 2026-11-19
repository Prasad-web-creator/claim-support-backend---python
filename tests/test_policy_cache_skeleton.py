"""A policy whose text was cached without its details must be extracted again, not reported invalid."""
from app.services.analysis_interactive_orchestrator import PolicyContext, has_extracted_policy_details

SKELETON = {
    "coveredDiseases": [], "excludedDiseases": [], "coveredTreatments": [], "excludedTreatments": [],
    "waitingPeriods": [], "subLimits": [], "coPaymentRules": [], "riders": [], "coverages": [], "exclusions": [],
}


def test_empty_skeleton_is_not_an_extraction():
    assert has_extracted_policy_details(SKELETON) is False
    assert has_extracted_policy_details({}) is False
    assert has_extracted_policy_details(None) is False


def test_user_continuity_answers_alone_are_not_an_extraction():
    j = dict(SKELETON, inceptionDate="2024-04-01", isRenewal=True, waitingPeriodStartDate="2024-04-01")
    assert has_extracted_policy_details(j) is False


def test_real_extraction_is_recognised():
    assert has_extracted_policy_details(dict(SKELETON, policyNumber="PGI/HL/26/4410287")) is True
    assert has_extracted_policy_details({"coveredTreatments": ["Cataract surgery"]}) is True


def test_policy_context_drops_skeleton_so_text_is_extracted_again():
    ctx = PolicyContext(cached_text="policy text", cached_json=SKELETON)
    assert ctx.has_cached_text and ctx.cached_json is None
    ctx = PolicyContext(cached_text="policy text", cached_json={"policyNumber": "X"})
    assert ctx.cached_json == {"policyNumber": "X"}
