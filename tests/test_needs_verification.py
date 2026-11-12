"""
"Needs Verification": coverage that depends on one fact the documents do not give
(for example LASIK under a "refractive error less than 7.5 dioptres" exclusion).

Covers the deterministic parts (consistency checks, decisive questions, applying
answers, report roll-up) and the session flow: the analysis is held back while
the user answers, then finished from the stored outcomes without another AI call.
"""

import pytest_asyncio

from tests.conftest import VALID_POLICY_JSON

import app.services.analysis_interactive_orchestrator as aio
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services.coverage.report_generator import generate_report
from app.services.coverage.verification import (
    NEEDS_VERIFICATION,
    apply_consistency_checks,
    apply_decisive_answers,
    pending_decisive_questions,
)

USER = "user-nv"

# An active policy period, so the rule engine itself decides nothing.
ACTIVE_POLICY_JSON = dict(VALID_POLICY_JSON, policyStartDate="2025-01-01", policyEndDate="2030-12-31")

REFRACTIVE_CLAUSE = {
    "section": "Section 5 - Exclusions",
    "quote": "5.12. Refractive error: Expenses for treatment for correction of eyesight due to refractive error less than 7.5 dioptres.",
    "effect": "Excludes",
    "explanation": "Excludes refractive error correction below 7.5 dioptres.",
    "decisive": True,
}
PREAMBLE_CLAUSE = {
    "section": "Section 1 - Preamble",
    "quote": "the Company agrees to indemnify the Insured Persons for Medically Necessary expenses",
    "effect": "Covers",
    "explanation": "General cover for hospitalisation.",
    "decisive": False,
}
OPD_CLAUSE = {
    "section": "Exclusion 5.20 - Out-patient treatment",
    "quote": "Expenses incurred for out-patient treatment are excluded.",
    "effect": "Excludes",
    "explanation": "Out-patient care is excluded; this case is an admission.",
    "decisive": False,
}


def lasik_question():
    return {
        "id": "refractive_error",
        "title": "Eye Power",
        "question": "What is the eye power (refractive error) being corrected?",
        "options": ["Less than 7.5 dioptres", "7.5 dioptres or more"],
        "outcomes": [
            {
                "option": "Less than 7.5 dioptres",
                "coverageStatus": "Not Covered",
                "coverageStatusReason": "LASIK for refractive error below 7.5 dioptres is excluded under Section 5.12.",
                "financialDecision": "0% covered. 100% patient financial responsibility under Exclusion 5.12.",
            },
            {
                "option": "7.5 dioptres or more",
                "coverageStatus": "Covered",
                "coverageStatusReason": "LASIK for refractive error of 7.5 dioptres or more is not excluded by Section 5.12 and is covered.",
                "financialDecision": "Covered up to the Sum Insured. Co-payment nil for this patient.",
            },
        ],
    }


def lasik_item(**overrides):
    item = {
        "item": "lasik surgery",
        "itemType": "Procedure",
        "coverageStatus": NEEDS_VERIFICATION,
        "coverageStatusReason": "LASIK is covered only if the refractive error is 7.5 dioptres or more; below that it is excluded.",
        "verificationCondition": "Covered only if your eye power is 7.5 dioptres or more.",
        "verificationClause": "Section 5.12 - Refractive error",
        "decisiveQuestion": lasik_question(),
        "policyClauses": [REFRACTIVE_CLAUSE, PREAMBLE_CLAUSE],
        "policyClauseDecision": NEEDS_VERIFICATION,
    }
    item.update(overrides)
    return item


# ──────────────────────────────────────────────────────────────────────────────
# Consistency checks
# ──────────────────────────────────────────────────────────────────────────────

def test_covered_with_deciding_exclusion_becomes_needs_verification():
    """The reported bug: a decisive exclusion clause, yet the status said Covered."""
    item = lasik_item(
        coverageStatus="Covered",
        coverageStatusReason="Your treatment for lasik surgery is covered.",
        verificationCondition="",
        decisiveQuestion=None,
        policyClauseDecision="Not Covered",
    )
    [checked] = apply_consistency_checks([item])
    assert checked["coverageStatus"] == NEEDS_VERIFICATION
    assert checked["isCovered"] is False
    assert checked["verificationClause"] == "Section 5 - Exclusions"
    assert "could not be confirmed" in checked["coverageStatusReason"]


def test_exclusion_with_stated_exception_stays_covered():
    """A decisive exclusion the clause search itself concluded Covered (a stated exception)."""
    item = lasik_item(coverageStatus="Covered", decisiveQuestion=None, policyClauseDecision="Covered")
    [checked] = apply_consistency_checks([item])
    assert checked["coverageStatus"] == "Covered"


def test_non_decisive_exclusion_stays_covered():
    item = {
        "item": "appendectomy",
        "coverageStatus": "Covered",
        "policyClauses": [OPD_CLAUSE, PREAMBLE_CLAUSE],
        "policyClauseDecision": "Covered",
    }
    [checked] = apply_consistency_checks([item])
    assert checked["coverageStatus"] == "Covered"


def test_stated_copay_makes_item_partially_covered():
    item = {
        "item": "cataract",
        "coverageStatus": "Covered",
        "coverageStatusReason": "Covered subject to a 20% mandatory co-payment as the insured person is aged 42 at entry.",
    }
    [checked] = apply_consistency_checks([item])
    assert checked["coverageStatus"] == "Partially Covered"


def test_nil_or_conditional_copay_keeps_item_covered():
    for text in (
        "Covered 100% up to the Sum Insured. No co-payment or deductible applies.",
        "Co-payment Nil (20% applies if Insured Person is aged 61 years or above at entry).",
        "Covered. 0% co-pay under this plan.",
    ):
        [checked] = apply_consistency_checks([{"item": "x", "coverageStatus": "Covered", "financialDecision": text}])
        assert checked["coverageStatus"] == "Covered", text


def test_item_settled_by_users_answer_is_not_rechecked():
    item = lasik_item(coverageStatus="Covered", verificationResolved=True, policyClauseDecision="Needs Verification")
    [checked] = apply_consistency_checks([item])
    assert checked["coverageStatus"] == "Covered"


# ──────────────────────────────────────────────────────────────────────────────
# Decisive questions and answers
# ──────────────────────────────────────────────────────────────────────────────

def test_decisive_question_is_prefixed_and_gets_dont_know():
    [q] = pending_decisive_questions([lasik_item()])
    assert q["id"] == "verify_refractive_error"
    assert q["type"] == "single_choice"
    assert q["options"][-1] == "Don't know"
    assert q["category"] == "verification"


def test_decisive_questions_are_deduplicated_capped_and_never_repeated():
    items = [lasik_item(item=f"item {i}", decisiveQuestion=dict(lasik_question(), id=f"fact_{i}")) for i in range(5)]
    items.append(lasik_item(item="same fact"))
    items.append(lasik_item(item="same fact again"))
    assert len(pending_decisive_questions(items)) == 5
    assert pending_decisive_questions([lasik_item(), lasik_item(item="lasik left eye")]) == pending_decisive_questions([lasik_item()])
    assert pending_decisive_questions([lasik_item()], asked_ids={"verify_refractive_error"}) == []


def test_only_needs_verification_items_ask_questions():
    assert pending_decisive_questions([lasik_item(coverageStatus="Not Covered")]) == []
    assert pending_decisive_questions([lasik_item(decisiveQuestion={"question": "only one option", "options": ["a"]})]) == []


def test_answers_apply_the_stored_outcomes():
    covered, unresolved = apply_decisive_answers([lasik_item()], {"verify_refractive_error": "7.5 dioptres or more"})
    assert unresolved == []
    assert covered[0]["coverageStatus"] == "Covered"
    assert covered[0]["verificationResolved"] is True
    assert covered[0]["verificationAnswer"] == "7.5 dioptres or more"

    not_covered, _ = apply_decisive_answers([lasik_item()], {"verify_refractive_error": "Less than 7.5 dioptres"})
    assert not_covered[0]["coverageStatus"] == "Not Covered"
    assert "5.12" in not_covered[0]["coverageStatusReason"]


def test_dont_know_keeps_needs_verification():
    items, unresolved = apply_decisive_answers([lasik_item()], {"verify_refractive_error": "Don't know"})
    assert unresolved == []
    assert items[0]["coverageStatus"] == NEEDS_VERIFICATION


def test_answer_without_an_outcome_is_unresolved():
    q = dict(lasik_question(), outcomes=[])
    items, unresolved = apply_decisive_answers([lasik_item(decisiveQuestion=q)], {"verify_refractive_error": "7.5 dioptres or more"})
    assert unresolved == ["verify_refractive_error"]
    assert items[0]["coverageStatus"] == NEEDS_VERIFICATION


# ──────────────────────────────────────────────────────────────────────────────
# Report roll-up
# ──────────────────────────────────────────────────────────────────────────────

def _report(comparison, business_rule_results=None):
    return generate_report(
        policy_json=dict(VALID_POLICY_JSON),
        prescription_json={"diagnosis": "lasik surgery", "isManual": True, "manualText": "lasik surgery"},
        business_rule_results=business_rule_results or {},
        coverage_analysis={"comparison": comparison, "overallStatus": "Covered"},
        processing_time_ms=0,
    )


def test_report_with_needs_verification_item():
    report = _report([lasik_item(), {"item": "consultation", "coverageStatus": "Covered"}])
    assert report["overallStatus"] == NEEDS_VERIFICATION
    assert report["coverageBreakdown"] == {
        "covered": 1, "partiallyCovered": 0, "notCovered": 0, "needsVerification": 1, "total": 2,
    }
    assert report["dominanceScore"] == 50.0
    card = next(c for c in report["comparison"] if c["item"] == "lasik surgery")
    assert card["coverageStatus"] == NEEDS_VERIFICATION
    assert card["verificationCondition"] == "Covered only if your eye power is 7.5 dioptres or more."
    assert card["verificationClause"] == "Section 5.12 - Refractive error"
    assert "lasik surgery" not in report["excludedItems"]


def test_report_without_needs_verification_is_unchanged():
    report = _report([{"item": "appendectomy", "coverageStatus": "Covered"}])
    assert report["overallStatus"] == "Covered"
    assert report["dominanceScore"] == 100.0
    assert report["coverageBreakdown"]["needsVerification"] == 0


def test_rule_engine_rejection_overrides_needs_verification():
    report = _report(
        [lasik_item()],
        business_rule_results={"blockers": ["Treatment date is after policy expiry - Policy period ended"]},
    )
    assert report["overallStatus"] == "Not Covered"
    assert report["comparison"][0]["coverageStatus"] == "Not Covered"


# ──────────────────────────────────────────────────────────────────────────────
# Session flow
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def flow(monkeypatch, db):
    state = {"calls": 0, "result": None}

    async def fake_process_document(doc_id, user_id, doc_label="Document"):
        return f"{doc_label} TEXT"

    async def fake_extract_policy(text):
        return {"extractedJson": dict(ACTIVE_POLICY_JSON)}

    async def fake_extract_rx(text):
        return {"extractedJson": {"diagnosis": "lasik surgery", "procedures": [{"name": "lasik surgery"}]}}

    async def fake_analyze_coverage(**kwargs):
        state["calls"] += 1
        return state["result"]

    async def fake_ensure_continuity(policy_json, policy_text):
        return False

    async def fake_background(**kwargs):
        return None

    monkeypatch.setattr(aio, "_process_document", fake_process_document)
    monkeypatch.setattr(aio, "extract_policy_details", fake_extract_policy)
    monkeypatch.setattr(aio, "extract_prescription_details", fake_extract_rx)
    monkeypatch.setattr(aio, "analyze_coverage", fake_analyze_coverage)
    monkeypatch.setattr(aio, "ensure_continuity_fields", fake_ensure_continuity)
    monkeypatch.setattr(aio, "_background_post_processing", fake_background)

    state["result"] = {
        "next_action": "generate_report",
        "confidence": 85,
        "analysis": {"overallStatus": NEEDS_VERIFICATION, "summary": "Depends on eye power.", "comparison": [lasik_item()]},
    }

    async def start():
        policy = Policy(user_id=USER, policy_name="Policy A", insurance_company="TestInsurer", grid_fs_file_id="file-policy")
        await policy.insert()
        rx = Prescription(user_id=USER, is_manual=True, grid_fs_file_id="", extracted_prescription_text="lasik surgery")
        await rx.insert()
        return await aio.start_analysis_session(user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id))

    state["start"] = start
    return state


async def test_manual_prescription_is_asked_the_decisive_question(flow):
    started = await flow["start"]()

    assert started["status"] == "needs_clarification"
    [q] = started["questions"]
    assert q["id"] == "verify_refractive_error"
    assert "outcomes" not in q                      # outcomes stay on the server
    assert q["options"][-1] == "Don't know"

    session = await AnalysisSession.get(started["sessionId"])
    assert session.status == "waiting_for_user"
    assert session.pending_analysis["questionIds"] == ["verify_refractive_error"]
    assert await AnalysisReport.find(AnalysisReport.user_id == USER).count() == 0


def _accident_and_admission_item():
    return lasik_item(
        item="jaw fracture treatment",
        verificationCondition="Covered only if caused by an accident and the patient is admitted to hospital.",
        verificationClause="Section 5.17 - Dental treatment",
        decisiveQuestion=None,
        decisiveQuestions=[
            {"id": "accident_cause", "question": "Was the jaw fracture caused by an accident?", "options": ["Yes", "No"]},
            {"id": "admitted", "question": "Is / was the patient admitted to hospital for it?", "options": ["Yes", "No"]},
        ],
        policyClauses=[],
    )


def test_item_with_several_facts_asks_one_question_per_fact():
    qs = pending_decisive_questions([_accident_and_admission_item()])
    assert [q["id"] for q in qs] == ["verify_accident_cause", "verify_admitted"]
    assert all(q["options"][-1] == "Don't know" for q in qs)


def test_several_facts_all_dont_know_stays_needs_verification():
    items, unresolved = apply_decisive_answers(
        [_accident_and_admission_item()],
        {"verify_accident_cause": "Don't know", "verify_admitted": "Don't know"},
    )
    assert unresolved == []
    assert items[0]["coverageStatus"] == NEEDS_VERIFICATION


async def test_several_facts_answered_re_run_the_analysis_with_the_answers(flow, monkeypatch):
    async def jaw_rx(text):
        return {"extractedJson": {"diagnosis": "jaw fracture treatment"}}

    monkeypatch.setattr(aio, "extract_prescription_details", jaw_rx)
    flow["result"] = {"next_action": "generate_report", "confidence": 85,
                      "analysis": {"comparison": [_accident_and_admission_item()]}}
    started = await flow["start"]()
    assert [q["id"] for q in started["questions"]] == ["verify_accident_cause", "verify_admitted"]

    flow["result"] = {"next_action": "generate_report", "confidence": 95,
                      "analysis": {"comparison": [{"item": "jaw fracture treatment", "coverageStatus": "Covered"}]}}
    done = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER,
        answers={"verify_accident_cause": "Yes", "verify_admitted": "Yes"},
    )
    assert flow["calls"] == 2                      # the AI decided with both answers
    assert done["overallStatus"] == "Covered"
    answered = {qa["Question_ID"]: qa["User_Answer"] for qa in done["clarificationAnswersUsed"]}
    assert answered == {"verify_accident_cause": "Yes", "verify_admitted": "Yes"}


async def test_follow_up_question_is_asked_after_the_answers(flow, monkeypatch):
    """When the answers reveal another missing fact, the app asks again instead of assuming."""
    started = await flow["start"]()
    follow_up = lasik_item(decisiveQuestion={
        "id": "eye_condition", "question": "Is there any other eye condition being treated?",
        "options": ["Yes", "No"],
    })
    flow["result"] = {"next_action": "generate_report", "confidence": 80,
                      "analysis": {"comparison": [follow_up]}}
    session = await AnalysisSession.get(started["sessionId"])
    session.pending_analysis = {}          # the first answer goes back to the AI
    await session.save()

    again = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER, answers={"verify_refractive_error": "7.5 dioptres or more"},
    )
    assert again["status"] == "needs_clarification"
    assert again["questions"][0]["id"] == "verify_eye_condition"


async def test_bariatric_surgery_reaches_the_question_not_a_builtin_exclusion(flow, monkeypatch):
    """The reported case: bariatric surgery was blocked by a built-in exclusion list before the AI ran."""
    async def bariatric_rx(text):
        return {"extractedJson": {"diagnosis": "bariatric surgery", "procedures": [{"name": "bariatric surgery"}]}}

    monkeypatch.setattr(aio, "extract_prescription_details", bariatric_rx)
    bmi_question = {
        "id": "bmi",
        "question": "What is the patient's BMI (Body Mass Index)?",
        "options": ["40 or more", "35 to 39.9 with serious illness such as diabetes", "Below 35"],
        "outcomes": [
            {"option": "40 or more", "coverageStatus": "Covered", "coverageStatusReason": "BMI 40 or more meets Section 5.3."},
            {"option": "35 to 39.9 with serious illness such as diabetes", "coverageStatus": "Covered", "coverageStatusReason": "Meets Section 5.3."},
            {"option": "Below 35", "coverageStatus": "Not Covered", "coverageStatusReason": "Excluded under Section 5.3."},
        ],
    }
    flow["result"] = {
        "next_action": "generate_report",
        "confidence": 85,
        "analysis": {"comparison": [lasik_item(
            item="bariatric surgery",
            verificationCondition="Covered only if BMI is 40 or more, or 35 or more with severe illness.",
            verificationClause="Section 5.3 - Obesity / weight control",
            decisiveQuestion=bmi_question,
            policyClauses=[],
        )]},
    }

    started = await flow["start"]()
    assert started["status"] == "needs_clarification"
    assert started["questions"][0]["id"] == "verify_bmi"

    done = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER, answers={"verify_bmi": "40 or more"},
    )
    assert done["overallStatus"] == "Covered"
    assert "statutory" not in str(done).lower()


async def test_no_question_when_rule_engine_already_decides(flow, monkeypatch):
    """A policy without dates goes to manual review; asking for the eye power would be pointless."""
    async def undated_policy(text):
        return {"extractedJson": dict(VALID_POLICY_JSON)}

    monkeypatch.setattr(aio, "extract_policy_details", undated_policy)
    result = await flow["start"]()

    assert result["status"] == "complete"
    assert result["overallStatus"] == "Manual Review Required"
    assert not (await AnalysisSession.get(result["sessionId"])).pending_analysis


async def test_answer_completes_without_another_ai_call(flow):
    started = await flow["start"]()
    assert flow["calls"] == 1

    result = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER,
        answers={"verify_refractive_error": "Less than 7.5 dioptres"},
    )

    assert flow["calls"] == 1
    assert result["status"] == "complete"
    assert result["overallStatus"] == "Not Covered"
    assert result["comparison"][0]["coverageStatus"] == "Not Covered"
    assert result["clarificationAnswersUsed"][0]["User_Answer"] == "Less than 7.5 dioptres"

    session = await AnalysisSession.get(started["sessionId"])
    assert session.status == "completed"
    assert session.pending_analysis == {}
    assert session.report_id == str(result["_id"])


async def test_dont_know_answer_issues_a_needs_verification_report(flow):
    started = await flow["start"]()
    result = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER, answers={"verify_refractive_error": "Don't know"},
    )

    assert flow["calls"] == 1
    assert result["overallStatus"] == NEEDS_VERIFICATION
    assert result["coverageBreakdown"]["needsVerification"] == 1
    card = result["comparison"][0]
    assert card["coverageStatus"] == NEEDS_VERIFICATION
    assert card["verificationCondition"]

    stored = await AnalysisReport.get(result["_id"])
    assert stored.overall_status == NEEDS_VERIFICATION


async def test_answer_without_outcome_falls_back_to_the_ai(flow):
    q = dict(lasik_question(), outcomes=[])
    flow["result"] = {
        "next_action": "generate_report",
        "confidence": 85,
        "analysis": {"overallStatus": NEEDS_VERIFICATION, "comparison": [lasik_item(decisiveQuestion=q)]},
    }
    started = await flow["start"]()

    flow["result"] = {
        "next_action": "generate_report",
        "confidence": 95,
        "analysis": {"overallStatus": "Covered", "comparison": [{"item": "lasik surgery", "coverageStatus": "Covered"}]},
    }
    result = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER, answers={"verify_refractive_error": "7.5 dioptres or more"},
    )

    assert flow["calls"] == 2
    assert result["status"] == "complete"
    assert result["overallStatus"] == "Covered"


async def test_question_is_never_asked_twice(flow):
    """The AI returning the same decisive question after it was answered issues the report."""
    started = await flow["start"]()
    session = await AnalysisSession.get(started["sessionId"])
    session.pending_analysis = {}          # force the AI path on resume
    await session.save()

    result = await aio.resume_analysis_session(
        session_id=started["sessionId"], user_id=USER, answers={"verify_refractive_error": "Don't know"},
    )

    assert flow["calls"] == 2
    assert result["status"] == "complete"
    assert result["overallStatus"] == NEEDS_VERIFICATION
