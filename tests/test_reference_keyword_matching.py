"""
Reference-catalogue keyword matching.

The advisories shown to a claimant ("2-year waiting period", "permanent
exclusion") are driven by these keywords, so a keyword matching inside an
unrelated medical word puts a false warning in front of the user.
"""

import pytest

from app.data.reference import find_matching_keywords, keyword_matches
from app.data.reference.reference_benchmarks import (
    SPECIFIC_2_YEAR_WAITING_CONDITIONS,
    PERMANENT_EXCLUSIONS_CATALOG,
)

# A real ACL/meniscus report: no joint replacement, no cataract, no lump.
ACL_REPORT = (
    "Complete ACL tear with features consistent with a pivot-shift injury pattern, "
    "vertical tear involving the posterior horn of the medial meniscus, bone "
    "contusions/marrow edema, moderate knee joint effusion. Arthroscopic ACL "
    "reconstruction and meniscus repair advised. Three views obtained. "
    "Orthopedic follow-up in two weeks."
)


def _categories_detected(corpus: str) -> list:
    return [
        cond["category"]
        for cond in SPECIFIC_2_YEAR_WAITING_CONDITIONS
        if find_matching_keywords(cond["keywords"], corpus)
    ]


# ──────────────────────────────────────────────────────────────────────────────
# The false positives this matching exists to prevent
# ──────────────────────────────────────────────────────────────────────────────

def test_arthroscopy_does_not_trigger_a_joint_replacement_advisory():
    """"thr" must not match inside arthroscopic/three/orthopedic."""
    assert "Knee / Hip Joint Replacement" not in _categories_detected(ACL_REPORT)


def test_an_acl_report_raises_no_waiting_period_advisory_at_all():
    assert _categories_detected(ACL_REPORT) == []


@pytest.mark.parametrize(
    "keyword,text",
    [
        ("thr", "arthroscopic repair performed"),
        ("thr", "three views obtained"),
        ("tkr", "atkrol tablets"),
        ("iol", "biological markers were raised"),
        ("lump", "lumpectomy performed"),
        ("cyst", "cystic fibrosis screening"),
    ],
)
def test_keywords_never_match_inside_a_longer_word(keyword, text):
    assert not keyword_matches(keyword, text)


# ──────────────────────────────────────────────────────────────────────────────
# Real mentions must still be detected
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "keyword,text",
    [
        ("thr", "Patient underwent THR in 2024"),
        ("tkr", "advised TKR, left knee"),
        ("iol", "IOL implant placed"),
        ("cyst", "popliteal cysts noted"),          # plural
        ("stone", "multiple stones in the gallbladder"),
        ("knee replacement", "total knee-replacement done"),  # separator
        ("excision biopsy", "excision biopsy of the lesion"),  # multi-word
    ],
)
def test_real_mentions_are_still_matched(keyword, text):
    assert keyword_matches(keyword, text)


def test_joint_replacement_report_still_raises_the_advisory():
    corpus = "Severe osteoarthritis, total knee replacement (TKR) advised."
    assert "Knee / Hip Joint Replacement" in _categories_detected(corpus)


def test_cataract_surgery_still_raises_the_advisory():
    corpus = "Mature cataract, phacoemulsification with IOL implantation planned."
    assert "Cataract" in _categories_detected(corpus)


def test_permanent_exclusions_still_match_their_own_terms():
    """Each catalogued exclusion is found in text containing its own keyword."""
    unmatched = []
    for excl in PERMANENT_EXCLUSIONS_CATALOG:
        keyword = excl["keywords"][0]
        corpus = f"Patient notes mention {keyword} in the history."
        if not find_matching_keywords(excl["keywords"], corpus):
            unmatched.append(excl["name"])
    assert unmatched == []


# ──────────────────────────────────────────────────────────────────────────────
# Shape of the helpers
# ──────────────────────────────────────────────────────────────────────────────

def test_matching_is_case_insensitive():
    assert keyword_matches("cataract", "CATARACT surgery advised")


def test_empty_input_matches_nothing():
    assert find_matching_keywords(["cyst"], "") == []
    assert find_matching_keywords(None, "cyst noted") == []
    assert not keyword_matches("", "cyst noted")


def test_matches_are_returned_in_catalogue_order():
    found = find_matching_keywords(
        ["cataract", "iol", "intraocular lens"],
        "intraocular lens and IOL used after cataract surgery",
    )
    assert found == ["cataract", "iol", "intraocular lens"]
