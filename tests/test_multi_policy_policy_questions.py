"""
The new / renewed policy questions inside a multi-policy analysis.

Each policy runs the real single-policy pipeline in its own child session, so the
same decision tree applies per policy: one policy can be several rounds deep in
questions while its siblings finish. Only the leaves (text, LLM calls) are stubbed.
"""

from datetime import date, datetime

import pytest_asyncio
from beanie import PydanticObjectId as ObjectId

import app.services.analysis_interactive_orchestrator as aio
import app.services.multi_policy_orchestrator as mpo
from app.models.multi_policy_session import MultiPolicyAnalysisSession
from app.models.policy import Policy
from app.models.prescription import Prescription

USER = "user-multi-questions"
CURRENT_START = "2026-04-01"

YES = "Yes, renewed every year without a break"
NO = "No, there was a break"


def extracted(**continuity):
    return {
        "insuranceCompany": "TestInsurer",
        "policyType": "Health",
        "coveredTreatments": ["Appendectomy"],
        "coverageAmount": 500000,
        "policyStartDate": CURRENT_START,
        "policyEndDate": "2027-03-31",
        "continuity": dict({"version": 2, "policyStartDate": CURRENT_START, "continuityEvidence": []}, **continuity),
    }


RENEWAL_NO_BREAK = extracted(
    isRenewal=True, renewalInformation="Renewal - Individual Health", previousPolicyNumber="P-2025-0001",
    previousPolicyStartDate="2025-04-01", previousPolicyEndDate="2026-03-31",
    renewedWithoutBreak=True, renewalBreakStatement="Renewed without break in coverage",
)
FRESH = extracted(isRenewal=False, renewalInformation="Fresh")
SILENT = extracted()


@pytest_asyncio.fixture
async def multi(monkeypatch, db):
    state = {"ids": {}}

    async def fake_rx_text(ctx, user_id):
        return "PRESCRIPTION TEXT"

    async def fake_extract_rx(text):
        return {"extractedJson": {"diagnosis": "Acute appendicitis", "procedures": [{"name": "Appendectomy"}],
                                  "visitDate": "2026-10-10", "hospitalizationRequired": True}}

    async def fake_analyze_coverage(**kwargs):
        return {"next_action": "generate_report", "confidence": 90,
                "analysis": {"overallStatus": "Covered", "comparison": [{"item": "Appendectomy", "coverageStatus": "Covered"}]}}

    async def fake_ensure_continuity(pj, text):
        return False

    async def fake_background(**kwargs):
        return None

    monkeypatch.setattr(mpo, "_load_prescription_text", fake_rx_text)
    monkeypatch.setattr(mpo, "extract_prescription_details", fake_extract_rx)
    monkeypatch.setattr(aio, "analyze_coverage", fake_analyze_coverage)
    monkeypatch.setattr(aio, "ensure_continuity_fields", fake_ensure_continuity)
    monkeypatch.setattr(aio, "_background_post_processing", fake_background)

    async def add_policy(label, policy_json, inception, is_renewal, continuous=None):
        policy = Policy(user_id=USER, policy_name=f"Policy {label}", insurance_company="TestInsurer",
                        grid_fs_file_id=f"file-{label}", extracted_policy_text="POLICY TEXT " * 10,
                        extracted_policy_json=policy_json, inception_date=inception,
                        is_renewal=is_renewal, continuous_renewal=continuous)
        await policy.insert()
        state["ids"][label] = str(policy.id)

    async def start():
        rx = Prescription(user_id=USER, is_manual=False, grid_fs_file_id="file-rx")
        await rx.insert()
        result = await mpo.start_multi_policy_analysis(USER, str(rx.id), list(state["ids"].values()))
        state["parent_id"] = result["sessionId"]
        return result

    async def answer(label, answers):
        return await mpo.resume_policy_analysis(state["parent_id"], state["ids"][label], USER, answers)

    def entry(result, label):
        return next(e for e in result["policyAnalyses"] if e["policyId"] == state["ids"][label])

    async def record(label):
        return await Policy.get(ObjectId(state["ids"][label]))

    async def continuity(label):
        parent = await MultiPolicyAnalysisSession.get(ObjectId(state["parent_id"]))
        results, _ = await mpo.evaluate_session_continuity(parent)
        return results[state["ids"][label]]

    state.update(add_policy=add_policy, start=start, answer=answer, entry=entry, record=record, continuity=continuity)
    return state


async def test_each_policy_follows_its_own_questions(multi):
    # A: entered New, document says renewal (renewed without break).
    # B: entered Renewed, document says new ("Fresh").
    # C: entered New, document silent: nothing to ask.
    await multi["add_policy"]("A", RENEWAL_NO_BREAK, datetime(2026, 3, 20), is_renewal=False)
    await multi["add_policy"]("B", FRESH, datetime(2022, 4, 1), is_renewal=True, continuous=True)
    await multi["add_policy"]("C", SILENT, datetime(2026, 4, 1), is_renewal=False)

    result = await multi["start"]()
    a, b, c = (multi["entry"](result, x) for x in "ABC")
    assert a["status"] == "waiting_for_user"
    assert [q["id"] for q in a["questions"]] == ["policy_new_or_renewed_conflict"]
    assert a["questions"][0]["question"].startswith("You selected New policy")
    assert b["status"] == "waiting_for_user"
    assert b["questions"][0]["question"].startswith("You selected Renewed policy")
    assert c["status"] == "completed"

    # ── A: Renewed → first start + break on one screen ────────────────────────
    result = await multi["answer"]("A", {"policy_new_or_renewed_conflict": "Renewed policy"})
    a = multi["entry"](result, "A")
    assert a["status"] == "waiting_for_user"
    assert [q["id"] for q in a["questions"]] == ["policy_first_start_date", "policy_renewed_without_break"]
    # The date picker hints reach the app through the multi-policy payload too.
    assert a["questions"][0]["initialDate"] == "2025-04-01"
    assert a["questions"][0]["maxDate"] == CURRENT_START

    # Later date and "there was a break": the document says no break → break conflict first.
    result = await multi["answer"]("A", {"policy_first_start_date": "01-Apr-2026", "policy_renewed_without_break": NO})
    a = multi["entry"](result, "A")
    assert [q["id"] for q in a["questions"]] == ["policy_renewal_break_conflict"]

    # Use the document (no break) → now the first start date conflict.
    result = await multi["answer"]("A", {"policy_renewal_break_conflict": a["questions"][0]["options"][1]})
    a = multi["entry"](result, "A")
    [q] = a["questions"]
    assert q["id"] == "policy_inception_date_conflict"
    assert q["question"] == ("The document shows cover since 01 Apr 2025, but you entered 01 Apr 2026. "
                             "Which date should I use?")

    result = await multi["answer"]("A", {"policy_inception_date_conflict": q["options"][1]})
    assert multi["entry"](result, "A")["status"] == "completed"
    saved = await multi["record"]("A")
    assert saved.is_renewal is True and saved.continuous_renewal is True
    assert saved.inception_date.date() == date(2025, 4, 1)
    cont = await multi["continuity"]("A")
    assert cont["status"] == "Continuity Verified"
    assert cont["originalPolicyStartDate"] == "2025-04-01"

    # ── B: New → start date → matches the document ────────────────────────────
    result = await multi["answer"]("B", {"policy_new_or_renewed_conflict": "New policy (first year)"})
    b = multi["entry"](result, "B")
    assert [q["id"] for q in b["questions"]] == ["policy_new_start_date"]
    assert b["questions"][0]["initialDate"] == CURRENT_START

    result = await multi["answer"]("B", {"policy_new_start_date": "01-Apr-2026"})
    assert multi["entry"](result, "B")["status"] == "completed"
    saved = await multi["record"]("B")
    assert saved.is_renewal is False and saved.continuous_renewal is None
    assert saved.inception_date.date() == date(2026, 4, 1)
    cont = await multi["continuity"]("B")
    assert cont["status"] == "Continuity Verified"
    assert "new policy" in cont["summary"]

    # Sibling C was never touched by A's or B's questions.
    assert multi["entry"](result, "C")["status"] == "completed"
    assert result["status"] == "completed"


async def test_break_kept_against_the_document_shows_a_gap(multi):
    await multi["add_policy"]("A", RENEWAL_NO_BREAK, datetime(2026, 3, 20), is_renewal=False)
    await multi["start"]()
    await multi["answer"]("A", {"policy_new_or_renewed_conflict": "Renewed policy"})
    result = await multi["answer"]("A", {"policy_first_start_date": "01-Apr-2020", "policy_renewed_without_break": NO})
    q = multi["entry"](result, "A")["questions"][0]
    assert q["id"] == "policy_renewal_break_conflict"

    result = await multi["answer"]("A", {"policy_renewal_break_conflict": q["options"][0]})
    assert multi["entry"](result, "A")["status"] == "completed"
    assert (await multi["record"]("A")).continuous_renewal is False
    assert (await multi["continuity"]("A"))["status"] == "Coverage Gap Detected"
