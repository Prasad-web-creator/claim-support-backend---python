"""
New / renewed policy decision tree.

  New policy     → entered start date vs the document's start date (existing question).
                   If the document says it is a RENEWAL, the user is first asked to
                   confirm new or renewed.
  Renewed policy → without a break: the entered FIRST start date is checked against a
                   first start date the document itself states ("continuous coverage
                   since", "first inception date", ...). Never against the issue date or
                   the start date of the current renewal.
                   With a break: the document's start date is used; nothing is asked.
"""

from datetime import date, datetime

import pytest_asyncio

import app.services.analysis_interactive_orchestrator as aio
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services.analysis_interactive_orchestrator import _document_first_start_date

USER = "user-tree"

CURRENT_START = "2026-04-01"


def policy_json(**continuity):
    return {
        "insuranceCompany": "TestInsurer",
        "policyType": "Health",
        "coveredTreatments": ["Appendectomy"],
        "coverageAmount": 500000,
        "policyStartDate": CURRENT_START,
        "policyEndDate": "2027-03-31",
        "continuity": dict({"version": 1, "policyStartDate": CURRENT_START, "continuityEvidence": []}, **continuity),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Reading the document's first start date with care
# ──────────────────────────────────────────────────────────────────────────────

def test_first_start_date_is_read_when_the_document_names_it():
    pj = policy_json(continuousCoverageSince="2021-11-01", continuityEvidence=["Continuous coverage since: 01-Nov-2021"])
    assert _document_first_start_date(pj) == date(2021, 11, 1)


def test_current_period_start_is_never_taken_for_the_first_start():
    pj = policy_json(continuousCoverageSince=CURRENT_START, continuityEvidence=["Policy inception 01-Apr-2026"])
    assert _document_first_start_date(pj) is None
    later = policy_json(continuousCoverageSince="2026-06-01", continuityEvidence=["Member since 01-Jun-2026"])
    assert _document_first_start_date(later) is None


def test_first_start_date_needs_the_documents_own_wording():
    pj = policy_json(continuousCoverageSince="2021-11-01", continuityEvidence=["Period of insurance 01-Apr-2026 to 31-Mar-2027"])
    assert _document_first_start_date(pj) is None
    assert _document_first_start_date(policy_json(continuousCoverageSince="2021-11-01")) is None


def test_no_first_start_date_on_the_document():
    assert _document_first_start_date(policy_json()) is None


# ──────────────────────────────────────────────────────────────────────────────
# Analysis flow
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def tree(monkeypatch, db):
    state = {"policy_json": policy_json(), "calls": 0}

    async def fake_process_document(doc_id, user_id, doc_label="Document"):
        return f"{doc_label} TEXT"

    async def fake_extract_policy(text):
        return {"extractedJson": dict(state["policy_json"])}

    async def fake_extract_rx(text):
        return {"extractedJson": {"diagnosis": "Acute appendicitis", "procedures": [{"name": "Appendectomy"}],
                                  "visitDate": "2026-10-10", "hospitalizationRequired": True}}

    async def fake_analyze_coverage(**kwargs):
        state["calls"] += 1
        state["analyzed_policy_json"] = kwargs.get("policy_json")
        return {"next_action": "generate_report", "confidence": 90,
                "analysis": {"overallStatus": "Covered", "comparison": [{"item": "Appendectomy", "coverageStatus": "Covered"}]}}

    async def fake_ensure_continuity(pj, text):
        return False

    async def fake_background(**kwargs):
        return None

    monkeypatch.setattr(aio, "_process_document", fake_process_document)
    monkeypatch.setattr(aio, "extract_policy_details", fake_extract_policy)
    monkeypatch.setattr(aio, "extract_prescription_details", fake_extract_rx)
    monkeypatch.setattr(aio, "analyze_coverage", fake_analyze_coverage)
    monkeypatch.setattr(aio, "ensure_continuity_fields", fake_ensure_continuity)
    monkeypatch.setattr(aio, "_background_post_processing", fake_background)

    async def start(inception, is_renewal, continuous=None):
        policy = Policy(user_id=USER, policy_name="Policy A", insurance_company="TestInsurer",
                        grid_fs_file_id="file-policy", inception_date=inception,
                        is_renewal=is_renewal, continuous_renewal=continuous)
        await policy.insert()
        rx = Prescription(user_id=USER, is_manual=False, grid_fs_file_id="file-rx")
        await rx.insert()
        state["policy"] = policy
        return await aio.start_analysis_session(user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id))

    async def answer(started, answers):
        return await aio.resume_analysis_session(session_id=started["sessionId"], user_id=USER, answers=answers)

    state["start"] = start
    state["answer"] = answer
    return state


async def test_renewed_without_break_asks_when_document_states_another_first_start(tree):
    tree["policy_json"] = policy_json(continuousCoverageSince="2022-01-01",
                                      continuityEvidence=["Continuous coverage since 01/01/2022"])
    started = await tree["start"](datetime(2020, 1, 1), is_renewal=True, continuous=True)

    assert started["status"] == "needs_clarification"
    [q] = started["questions"]
    assert q["id"] == "policy_inception_date_conflict"
    assert "01-01-2020" in q["question"] and "01-01-2022" in q["question"]

    done = await tree["answer"](started, {"policy_inception_date_conflict": "Use policy document date (01-01-2022)"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2022-01-01"
    assert done["policyContinuity"]["originalPolicyStartDate"] == "2022-01-01"
    assert (await Policy.get(tree["policy"].id)).inception_date.date() == date(2022, 1, 1)


async def test_renewed_entered_date_kept_when_user_chooses_it(tree):
    tree["policy_json"] = policy_json(continuousCoverageSince="2022-01-01",
                                      continuityEvidence=["First inception date: 01/01/2022"])
    started = await tree["start"](datetime(2020, 1, 1), is_renewal=True, continuous=True)
    done = await tree["answer"](started, {"policy_inception_date_conflict": "Use my entered date (01-01-2020)"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2020-01-01"


async def test_renewed_no_question_when_document_only_shows_current_period(tree):
    tree["policy_json"] = policy_json(continuousCoverageSince=CURRENT_START,
                                      continuityEvidence=["Policy issued on 01-Apr-2026"])
    done = await tree["start"](datetime(2020, 1, 1), is_renewal=True, continuous=True)
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2020-01-01"


async def test_renewed_with_break_uses_document_date_without_asking(tree):
    tree["policy_json"] = policy_json(continuousCoverageSince="2022-01-01",
                                      continuityEvidence=["Continuous coverage since 01/01/2022"])
    done = await tree["start"](datetime(2020, 1, 1), is_renewal=True, continuous=False)
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


async def test_new_policy_matching_document_is_not_asked_anything(tree):
    done = await tree["start"](datetime(2026, 4, 1), is_renewal=False)
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


async def test_new_policy_but_document_says_renewal_confirmed_renewed(tree):
    tree["policy_json"] = policy_json(isRenewal=True, renewalInformation="Renewal - Policy Year 3",
                                      continuousCoverageSince="2024-04-01",
                                      continuityEvidence=["Insured with us since 01-Apr-2024"])
    started = await tree["start"](datetime(2026, 4, 1), is_renewal=False)

    [q] = started["questions"]
    assert q["id"] == "policy_new_or_renewed_conflict"
    assert "Renewal - Policy Year 3" in q["question"]

    done = await tree["answer"](started, {"policy_new_or_renewed_conflict": "Renewed policy"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["isRenewal"] is True
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2024-04-01"
    saved = await Policy.get(tree["policy"].id)
    assert saved.is_renewal is True
    assert saved.inception_date.date() == date(2024, 4, 1)


async def test_confirmed_renewal_without_stated_first_start_uses_current_start(tree):
    tree["policy_json"] = policy_json(previousPolicyNumber="SSGI/HLT/2025/0001")
    started = await tree["start"](datetime(2026, 4, 1), is_renewal=False)
    assert "previous policy number SSGI/HLT/2025/0001" in started["questions"][0]["question"]

    done = await tree["answer"](started, {"policy_new_or_renewed_conflict": "Renewed policy"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START
    assert "first start date is not known" in tree["analyzed_policy_json"]["waitingPeriodBasis"]


async def test_confirmed_new_then_asks_start_date_conflict(tree):
    tree["policy_json"] = policy_json(isRenewal=True, renewalInformation="Renewal")
    started = await tree["start"](datetime(2026, 3, 15), is_renewal=False)
    assert started["questions"][0]["id"] == "policy_new_or_renewed_conflict"

    follow_up = await tree["answer"](started, {"policy_new_or_renewed_conflict": "New policy (first year)"})
    assert follow_up["status"] == "needs_clarification"
    [q] = follow_up["questions"]
    assert q["id"] == "policy_start_date_conflict"
    assert tree["calls"] == 0

    done = await tree["answer"](started, {"policy_start_date_conflict": "Use policy document date (01-04-2026)"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


async def test_confirmed_new_with_matching_date_goes_straight_to_report(tree):
    tree["policy_json"] = policy_json(isRenewal=True)
    started = await tree["start"](datetime(2026, 4, 1), is_renewal=False)
    done = await tree["answer"](started, {"policy_new_or_renewed_conflict": "New policy (first year)"})
    assert done["status"] == "complete"
