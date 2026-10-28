"""
The AI policy clause search: only quotes that exist in the policy are kept, the
clauses reach the matching Coverage Decision card, and the decisive Not Covered
clauses become the report's major terms.
"""

from app.services.analysis_interactive_orchestrator import _apply_manual_prescription_defaults
from app.services.coverage.policy_clause_search import (
    _clean_result,
    attach_policy_clauses,
    comparison_from_search,
)
from app.services.coverage.report_generator import build_major_terms

POLICY_TEXT = """Waiting Periods
Maternity / Newborn
Not covered under this plan
D.18 Maternity (Excl18). Medical treatment expenses traceable to childbirth (including complicated deliveries and caesarean sections
incurred during hospitalisation) except ectopic pregnancy, and expenses towards miscarriage or termination of pregnancy.
"""

AI_RESULT = {
    "items": [{
        "item": "Maternity",
        "relatedTerms": ["pregnancy", "childbirth"],
        "directMention": True,
        "decision": "Not Covered",
        "reason": "Maternity is excluded under D.18.",
        "clauses": [
            {"section": "Waiting Periods", "quote": "Maternity / Newborn Not covered under this plan",
             "effect": "Excludes", "explanation": "Not covered under this plan.", "decisive": True},
            {"section": "D.18 Maternity (Excl18)",
             "quote": "D.18 Maternity (Excl18). Medical treatment expenses traceable to childbirth (including complicated "
                      "deliveries and caesarean sections incurred during hospitalisation) except ectopic pregnancy, and "
                      "expenses towards miscarriage or termination of pregnancy.",
             "effect": "Excludes", "explanation": "Permanent exclusion.", "decisive": True},
            {"section": "D.18", "quote": "Maternity is covered after 9 months of continuous cover.",
             "effect": "Covers", "explanation": "Invented.", "decisive": False},
        ],
    }]
}


def test_only_quotes_found_in_policy_are_kept():
    items = _clean_result(AI_RESULT, POLICY_TEXT)
    quotes = [c["quote"] for c in items[0]["clauses"]]
    assert quotes == [
        "Maternity / Newborn Not covered under this plan",
        AI_RESULT["items"][0]["clauses"][1]["quote"],
    ]


def test_clauses_reach_card_and_become_major_terms():
    items = _clean_result(AI_RESULT, POLICY_TEXT)
    comparison = attach_policy_clauses([{"item": "Maternity", "coverageStatus": "Not Covered"}], items)
    assert len(comparison[0]["policyClauses"]) == 2

    terms = build_major_terms(comparison, blockers=[])
    assert [t["title"] for t in terms] == ["Waiting Periods", "D.18 Maternity (Excl18)"]
    assert all(t["items"] == ["Maternity"] for t in terms)


def test_major_terms_fall_back_to_decisive_blockers():
    blockers = [
        "Maternity is permanently excluded under policy clause D.18 Maternity (Excl18). - D.18 Maternity (Excl18): \"...\"",
        "Initial 30-day waiting period: illnesses are not covered in the first 30 days.",
    ]
    terms = build_major_terms([{"item": "Maternity", "coverageStatus": "Not Covered"}], blockers)
    assert len(terms) == 1
    assert terms[0]["title"].startswith("Maternity is permanently excluded")


def test_cards_built_from_search_when_analysis_has_none():
    cards = comparison_from_search(_clean_result(AI_RESULT, POLICY_TEXT))
    assert cards[0]["coverageStatus"] == "Not Covered"
    assert "D.18 Maternity (Excl18)" in cards[0]["policyEvidence"]


def test_manual_check_uses_today_and_assumes_admission():
    rx = _apply_manual_prescription_defaults({"visitDate": "2020-01-01", "hospitalizationRequired": False}, "Influenza")
    from datetime import datetime
    assert rx["visitDate"] == datetime.now().strftime("%Y-%m-%d")
    assert rx["hospitalizationRequired"] is True and rx["admissionAssumed"] is True

    opd = _apply_manual_prescription_defaults({}, "Influenza OPD consultation")
    assert opd["hospitalizationRequired"] is False
