"""
A clarification question the user has answered is never shown again — not even
when the AI re-asks it under a different id.
"""

import app.services.analysis_interactive_orchestrator as aio
from app.models.analysis_session import AnalysisSession

from tests.test_single_policy_regression import single  # noqa: F401  (fixture)

ASK_ADMITTED = {
    "next_action": "ask_questions",
    "confidence": 40,
    "questions": [{
        "id": "hospital_admission",
        "title": "Admission",
        "question": "Was the patient admitted to hospital?",
        "type": "single_choice",
        "options": ["Yes", "No"],
    }],
}

REASKED_UNDER_NEW_ID = {
    "next_action": "ask_questions",
    "confidence": 40,
    "questions": [{
        "id": "admission_status",
        "title": "Admission status",
        "question": "Was the patient admitted to hospital ?",
        "type": "single_choice",
        "options": ["Yes", "No"],
    }],
}


async def _start(single):
    policy = await single.add_policy()
    rx = await single.add_prescription()
    return await aio.start_analysis_session(
        user_id="user-1", prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )


def test_same_wording_under_new_id_counts_as_asked():
    session = AnalysisSession(user_id="u", policy_id="p", prescription_id="r")
    session.questions.append(aio.ClarificationQuestion(
        id="hospital_admission", title="Admission",
        question="Was the patient admitted to hospital?", type="single_choice",
    ))
    assert aio._new_questions(session, REASKED_UNDER_NEW_ID["questions"]) == []
    assert aio._new_questions(session, [{
        "id": "pre_existing", "title": "History", "question": "Did you have this before the policy?",
    }])


async def test_reasked_question_is_not_shown_again_and_ai_decides(single):
    calls = []
    covered = single.coverage_result

    async def fake_analyze(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return ASK_ADMITTED
        if not kwargs.get("extra_instruction"):
            return REASKED_UNDER_NEW_ID
        return covered

    aio_analyze = aio.analyze_coverage
    aio.analyze_coverage = fake_analyze
    try:
        started = await _start(single)
        assert started["status"] == "needs_clarification"
        assert [q["id"] for q in started["questions"]] == ["hospital_admission"]

        result = await aio.resume_analysis_session(
            session_id=started["sessionId"], user_id="user-1",
            answers={"hospital_admission": "Yes"},
        )
    finally:
        aio.analyze_coverage = aio_analyze

    # The re-worded repeat was never sent to the user; the AI was asked to decide instead.
    assert result.get("status") != "needs_clarification"
    assert result.get("overallStatus")  # a report was issued
    assert calls[-1]["extra_instruction"] == aio.DECIDE_WITH_ANSWERS_INSTRUCTION
    assert len(calls) == 3
