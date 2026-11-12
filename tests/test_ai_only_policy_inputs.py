"""
Coverage is decided by the AI from the customer's own policy wording only.

Pins the audit fixes:
  1. The policy search never mixes built-in reference text into a policy's evidence.
  2. The AI receives the FULL policy text, not only snippets.
  3. The policy reading instructions name no example ailments as excluded.
  4. Past cases from other claims are not given to the AI.
  6. Reports carry no built-in benchmark comparison.
"""

import pytest_asyncio

import app.services.coverage.coverage_analysis as ca
from app.services.coverage.report_generator import REFERENCE_COMPARISON_DISABLED, generate_report
from app.services.llm.prompts import POLICY_EXTRACTION_PROMPT


@pytest_asyncio.fixture
async def chunks_db():
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient
    from app.models.rag_chunk import PolicyChunk

    client = AsyncMongoMockClient()
    await init_beanie(database=client["chunks_test"], document_models=[PolicyChunk])
    return PolicyChunk


def _chunk(PolicyChunk, chunk_id, policy_id, text):
    return PolicyChunk(chunk_id=chunk_id, policy_id=policy_id, insurer="All", product="All", text=text)


async def test_policy_search_never_returns_global_reference_text(chunks_db):
    from app.services.rag.providers.local_provider import LocalSemanticRetrievalProvider

    await _chunk(chunks_db, "own-1", "policy-1", "5.3 Obesity: surgical treatment of obesity unless BMI is 40 or more").insert()
    await _chunk(chunks_db, "glob-1", "GLOBAL_REFERENCE",
                 "Obesity treatment is permanently non-payable under standard health insurance policies").insert()

    results = await LocalSemanticRetrievalProvider().search("bariatric obesity surgery", {"policy_id": "policy-1"}, top_k=5)
    texts = " ".join(r["text"] for r in results)
    assert "unless BMI is 40" in texts
    assert "permanently non-payable" not in texts

    assert await LocalSemanticRetrievalProvider().search("obesity", {}, top_k=5) == []


async def test_ai_gets_full_policy_text_and_no_past_cases(monkeypatch):
    captured = {}
    policy_text = "SCHEDULE\n" + ("filler clause text. " * 2000) + "\n5.12 Refractive error: less than 7.5 dioptres."

    async def fake_ai(system_prompt, user_content, **kwargs):
        captured["user"] = user_content
        return {"extractedJson": {"next_action": "generate_report", "confidence": 90,
                                  "analysis": {"comparison": [{"item": "lasik", "coverageStatus": "Covered"}]}}}

    async def fake_search(*args, **kwargs):
        return []

    async def no_chunks(**kwargs):
        return []

    async def must_not_run(**kwargs):
        raise AssertionError("past cases must not be retrieved for the AI")

    import app.services.coverage.policy_clause_search as pcs
    from app.services.rag.policy_retrieval_service import PolicyRetrievalService
    from app.services.expert_learning.case_retrieval_service import SimilarCaseRetrievalService

    monkeypatch.setattr(ca, "extract_json_with_retry", fake_ai)
    monkeypatch.setattr(pcs, "search_policy_clauses", fake_search)
    monkeypatch.setattr(PolicyRetrievalService, "retrieve_policy_context", no_chunks)
    monkeypatch.setattr(SimilarCaseRetrievalService, "retrieve_similar_cases", must_not_run)

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
    assert report["referenceComparison"] == REFERENCE_COMPARISON_DISABLED
    assert report["referenceComparison"]                                 # non-empty: the app does not re-fetch
