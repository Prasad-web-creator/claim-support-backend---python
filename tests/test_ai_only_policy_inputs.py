"""
Coverage is decided by the AI from the customer's own policy wording only.

Checks full policy text in the coverage prompt and excludes reference data.
"""

import app.services.coverage.coverage_analysis as ca
from app.services.coverage.report_generator import generate_report
from app.services.llm.prompts import POLICY_EXTRACTION_PROMPT


async def test_ai_gets_full_policy_text(monkeypatch):
    captured = {}
    policy_text = "SCHEDULE\n" + ("filler clause text. " * 2000) + "\n5.12 Refractive error: less than 7.5 dioptres."

    async def fake_ai(system_prompt, user_content, **kwargs):
        captured["user"] = user_content
        return {"extractedJson": {"next_action": "generate_report", "confidence": 90,
                                  "analysis": {"comparison": [{"item": "lasik", "coverageStatus": "Covered"}]}}}

    async def fake_search(*args, **kwargs):
        return []

    import app.services.coverage.policy_clause_search as pcs

    monkeypatch.setattr(ca, "extract_json_with_retry", fake_ai)
    monkeypatch.setattr(pcs, "search_policy_clauses", fake_search)

    await ca.analyze_coverage(
        policy_text=policy_text,
        prescription_text="lasik surgery",
        business_rule_results={"blockers": [], "deterministicResult": {"status": "COVERED"}},
        prescription_json={"diagnosis": "lasik surgery", "isManual": True, "manualText": "lasik surgery"},
        policy_json={"insuranceCompany": "X"},
    )

    prompt = captured["user"]
    assert "FULL POLICY TEXT" in prompt
    assert "5.12 Refractive error: less than 7.5 dioptres." in prompt   # beyond the old 12,000-character cut
    assert "SIMILAR EXPERT CASES" not in prompt
    assert '"COVERED"' not in prompt                                    # no outcome label from the app checks


def test_policy_reading_instructions_name_no_excluded_ailments():
    lower = POLICY_EXTRACTION_PROMPT.lower()
    for word in ("obesity", "cosmetic", "change of gender", "cataract", "hernia", "d.2", "excl02"):
        assert word not in lower, word


def test_report_has_no_builtin_benchmark_comparison():
    report = generate_report(
        policy_json={"insuranceCompany": "X", "coverageAmount": 100000},
        prescription_json={"diagnosis": "appendicitis"},
        business_rule_results={},
        coverage_analysis={"comparison": [{"item": "appendicitis", "coverageStatus": "Covered"}]},
        processing_time_ms=0,
    )
    assert "referenceComparison" not in report


async def test_historical_report_omits_retired_evidence(db):
    from app.models.analysis_report import AnalysisReport

    report = AnalysisReport(user_id="historical-report-owner")
    await report.insert()
    comparison = [{"item": "Appendectomy", "coverageStatus": "Covered"}]
    await AnalysisReport.get_motor_collection().update_one(
        {"_id": report.id},
        {"$set": {"coverageAnalysis": {
            "comparison": comparison,
            "retrieved_evidence": [{"text": "Old index excerpt"}],
            "similar_expert_cases": [{"caseId": "old-case"}],
        }}},
    )

    stored_report = await AnalysisReport.get(report.id)
    assert stored_report.model_dump(by_alias=True)["coverageAnalysis"] == {"comparison": comparison}
