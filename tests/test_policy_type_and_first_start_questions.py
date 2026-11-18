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
    assert q["question"] == ("The document shows cover since 01 Jan 2022, but you entered 01 Jan 2020. "
                             "Which date should I use?")
    assert q["options"] == ["Use my entered date (01 Jan 2020)", "Use policy document date (01 Jan 2022)"]

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


# ══════════════════════════════════════════════════════════════════════════════
# Decision tree
#
#   Entered  | Document says     | Asked             | Answer  | Next
#   New      | renewal           | new or renewed?   | New     | entered vs document start date
#   New      | renewal           | new or renewed?   | Renewed | first start date + without a break? (one screen)
#                                                               → break conflict (only if the document states it)
#                                                               → first start conflict (without a break only)
#   Renewed  | new ("Fresh")     | new or renewed?   | New     | start date → start date conflict
#   Renewed  | new ("Fresh")     | new or renewed?   | Renewed | upload answers kept → conflicts as above
#   Renewed  | silent / renewal  | break conflict, then first start conflict (as needed)
#   New      | silent / new      | entered vs document start date
# ══════════════════════════════════════════════════════════════════════════════

YES = "Yes, renewed every year without a break"
NO = "No, there was a break"


def renewal_doc(**extra):
    base = dict(isRenewal=True, renewalInformation="Renewal - Individual Health",
                previousPolicyNumber="P-2025-0001",
                previousPolicyStartDate="2025-04-01", previousPolicyEndDate="2026-03-31")
    base.update(extra)
    return policy_json(**base)


def no_break_statement(**extra):
    return renewal_doc(renewedWithoutBreak=True, renewalBreakStatement="Renewed without break in coverage", **extra)


async def _entered_new_confirmed_renewed(tree):
    started = await tree["start"](datetime(2026, 3, 20), is_renewal=False)
    [q] = started["questions"]
    assert q["id"] == "policy_new_or_renewed_conflict"
    assert q["question"].startswith("You selected New policy, but the policy document shows it is a renewal")
    asked = await tree["answer"](started, {"policy_new_or_renewed_conflict": "Renewed policy"})
    assert asked["status"] == "needs_clarification"
    return started, asked


# ── Entered New, document says renewal, confirmed Renewed ─────────────────────

async def test_confirmed_renewed_asks_first_start_and_break_on_one_screen(tree):
    tree["policy_json"] = renewal_doc()
    started, asked = await _entered_new_confirmed_renewed(tree)

    first, brk = asked["questions"]
    assert first["id"] == "policy_first_start_date"
    assert first["type"] == "date" and first["options"] == []
    assert "The policy document shows cover since 01 Apr 2025." in first["question"]
    # The upload date (20 Mar 2026, a New policy's start date) is not reused.
    assert first["initialDate"] == "2025-04-01"
    assert first["maxDate"] == CURRENT_START
    assert brk["id"] == "policy_renewed_without_break"
    assert brk["options"] == [YES, NO]
    assert tree["calls"] == 0

    done = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2025", "policy_renewed_without_break": YES})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2025-04-01"
    assert done["policyContinuity"]["status"] == "Continuity Verified"
    saved = await Policy.get(tree["policy"].id)
    assert saved.is_renewal is True
    assert saved.continuous_renewal is True
    assert saved.inception_date.date() == date(2025, 4, 1)


async def test_later_first_start_than_document_asks_which_date(tree):
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    conflict = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2026", "policy_renewed_without_break": YES})
    [q] = conflict["questions"]
    assert q["id"] == "policy_inception_date_conflict"
    assert q["question"] == ("The document shows cover since 01 Apr 2025, but you entered 01 Apr 2026. "
                             "Which date should I use?")

    done = await tree["answer"](started, {"policy_inception_date_conflict": q["options"][1]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2025-04-01"
    assert done["policyContinuity"]["originalPolicyStartDate"] == "2025-04-01"
    assert (await Policy.get(tree["policy"].id)).inception_date.date() == date(2025, 4, 1)


async def test_later_first_start_kept_when_user_chooses_it(tree):
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    conflict = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2026", "policy_renewed_without_break": YES})
    done = await tree["answer"](started, {"policy_inception_date_conflict": conflict["questions"][0]["options"][0]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START
    assert done["policyContinuity"]["status"] == "Continuity Verified"


async def test_earlier_first_start_than_previous_period_is_accepted(tree):
    # The previous period only proves cover since 2025; it may go back further.
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    done = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2021", "policy_renewed_without_break": YES})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2021-04-01"


async def test_first_start_after_the_current_period_is_treated_as_its_start(tree):
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    conflict = await tree["answer"](started, {"policy_first_start_date": "10-May-2026", "policy_renewed_without_break": YES})
    assert "you entered 01 Apr 2026" in conflict["questions"][0]["question"]


async def test_break_answer_counts_from_current_start_without_date_conflict(tree):
    # The document does not state whether renewals had a break, so nothing more is asked.
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    done = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2026", "policy_renewed_without_break": NO})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START
    assert (await Policy.get(tree["policy"].id)).continuous_renewal is False


async def test_break_answer_with_earlier_first_start_is_a_gap(tree):
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    done = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2020", "policy_renewed_without_break": NO})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START
    assert done["policyContinuity"]["status"] == "Coverage Gap Detected"


async def _break_conflict_asked(tree):
    tree["policy_json"] = no_break_statement()
    started, _ = await _entered_new_confirmed_renewed(tree)
    conflict = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2020", "policy_renewed_without_break": NO})
    [q] = conflict["questions"]
    return started, q


async def test_break_answer_against_document_saying_no_break_asks(tree):
    _, q = await _break_conflict_asked(tree)
    assert q["id"] == "policy_renewal_break_conflict"
    assert q["question"] == ('The document shows this policy was renewed without a break ("Renewed without break in '
                             'coverage"), but you answered there was a break. Which should I use?')
    assert q["options"] == ["Use my answer (there was a break)", "Use policy document (renewed without a break)"]


async def test_break_conflict_document_answer_then_counts_from_first_start(tree):
    started, q = await _break_conflict_asked(tree)
    # 2020 is earlier than the previous period (2025), so no date conflict follows.
    done = await tree["answer"](started, {"policy_renewal_break_conflict": q["options"][1]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2020-04-01"
    assert done["policyContinuity"]["status"] == "Continuity Verified"
    assert (await Policy.get(tree["policy"].id)).continuous_renewal is True


async def test_break_conflict_my_answer_keeps_the_gap(tree):
    started, q = await _break_conflict_asked(tree)
    done = await tree["answer"](started, {"policy_renewal_break_conflict": q["options"][0]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START
    assert done["policyContinuity"]["status"] == "Coverage Gap Detected"


async def test_break_conflict_then_first_start_conflict(tree):
    # Both disagree: the break is settled first, then the first start date.
    tree["policy_json"] = no_break_statement()
    started, _ = await _entered_new_confirmed_renewed(tree)
    first = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2026", "policy_renewed_without_break": NO})
    assert first["questions"][0]["id"] == "policy_renewal_break_conflict"
    second = await tree["answer"](started, {"policy_renewal_break_conflict": first["questions"][0]["options"][1]})
    [q] = second["questions"]
    assert q["id"] == "policy_inception_date_conflict"
    done = await tree["answer"](started, {"policy_inception_date_conflict": q["options"][1]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2025-04-01"


async def test_no_break_answer_against_document_saying_break_asks(tree):
    tree["policy_json"] = renewal_doc(renewedWithoutBreak=False, renewalBreakStatement="Policy renewed after a break")
    started, _ = await _entered_new_confirmed_renewed(tree)
    conflict = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2025", "policy_renewed_without_break": YES})
    [q] = conflict["questions"]
    assert q["id"] == "policy_renewal_break_conflict"
    assert "there was a break in this policy's renewals" in q["question"]
    done = await tree["answer"](started, {"policy_renewal_break_conflict": q["options"][1]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


async def test_break_flag_without_matching_wording_is_ignored(tree):
    # The extracted flag alone is not enough: the document's own words must say it.
    tree["policy_json"] = renewal_doc(renewedWithoutBreak=True, renewalBreakStatement="Renewal - Individual Health")
    started, _ = await _entered_new_confirmed_renewed(tree)
    done = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2025", "policy_renewed_without_break": NO})
    assert done["status"] == "complete"


async def test_previous_period_with_a_gap_is_not_proof_of_continuous_cover(tree):
    tree["policy_json"] = renewal_doc(previousPolicyStartDate="2025-01-01", previousPolicyEndDate="2025-12-31")
    started, asked = await _entered_new_confirmed_renewed(tree)
    assert "The policy document shows" not in asked["questions"][0]["question"]
    done = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2026", "policy_renewed_without_break": YES})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


async def test_document_naming_its_first_start_date(tree):
    tree["policy_json"] = policy_json(isRenewal=True, renewalInformation="Renewal - Policy Year 3",
                                      continuousCoverageSince="2024-04-01",
                                      continuityEvidence=["Insured with us since 01-Apr-2024"])
    started, asked = await _entered_new_confirmed_renewed(tree)
    assert asked["questions"][0]["initialDate"] == "2024-04-01"
    # A first start date the document names is asked about when the entered date differs, even if earlier.
    conflict = await tree["answer"](started, {"policy_first_start_date": "01-Apr-2022", "policy_renewed_without_break": YES})
    assert conflict["questions"][0]["id"] == "policy_inception_date_conflict"


async def test_no_document_date_takes_the_users_date(tree):
    tree["policy_json"] = policy_json(previousPolicyNumber="SSGI/HLT/2025/0001")
    started = await tree["start"](datetime(2026, 4, 1), is_renewal=False)
    assert "previous policy number SSGI/HLT/2025/0001" in started["questions"][0]["question"]
    asked = await tree["answer"](started, {"policy_new_or_renewed_conflict": "Renewed policy"})
    assert "The policy document shows" not in asked["questions"][0]["question"]
    assert asked["questions"][0]["initialDate"] == CURRENT_START

    done = await tree["answer"](started, {"policy_first_start_date": "15-Jun-2023", "policy_renewed_without_break": YES})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2023-06-15"
    assert done["policyContinuity"]["originalPolicyStartDate"] == "2023-06-15"
    assert (await Policy.get(tree["policy"].id)).inception_date.date() == date(2023, 6, 15)


async def test_stale_break_answer_on_the_record_is_cleared(tree):
    tree["policy_json"] = renewal_doc()
    started = await tree["start"](datetime(2026, 3, 20), is_renewal=False, continuous=False)
    await tree["answer"](started, {"policy_new_or_renewed_conflict": "Renewed policy"})
    assert (await Policy.get(tree["policy"].id)).continuous_renewal is None


async def test_unreadable_answers_keep_the_document_date(tree):
    tree["policy_json"] = renewal_doc()
    started, _ = await _entered_new_confirmed_renewed(tree)
    done = await tree["answer"](started, {"policy_first_start_date": "not sure", "policy_renewed_without_break": "maybe"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2025-04-01"


# ── Entered Renewed, document explicitly says new ─────────────────────────────

def fresh_doc():
    return policy_json(isRenewal=False, renewalInformation="Fresh")


async def _entered_renewed_document_new(tree, continuous=True):
    started = await tree["start"](datetime(2022, 4, 1), is_renewal=True, continuous=continuous)
    assert started["status"] == "needs_clarification"
    [q] = started["questions"]
    assert q["id"] == "policy_new_or_renewed_conflict"
    assert q["question"] == ('You selected Renewed policy, but the policy document shows it is a new policy ("Fresh"). '
                             'Is this policy new or renewed?')
    return started


async def test_entered_renewed_confirmed_new_asks_start_date(tree):
    tree["policy_json"] = fresh_doc()
    started = await _entered_renewed_document_new(tree, continuous=False)
    asked = await tree["answer"](started, {"policy_new_or_renewed_conflict": "New policy (first year)"})
    [q] = asked["questions"]
    assert q["id"] == "policy_new_start_date"
    assert q["type"] == "date"
    assert q["initialDate"] == CURRENT_START
    saved = await Policy.get(tree["policy"].id)
    assert saved.is_renewal is False and saved.continuous_renewal is None

    done = await tree["answer"](started, {"policy_new_start_date": "01-Apr-2026"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["isRenewal"] is False
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START
    assert done["policyContinuity"]["status"] == "Continuity Verified"
    assert "new policy" in done["policyContinuity"]["summary"]
    assert (await Policy.get(tree["policy"].id)).inception_date.date() == date(2026, 4, 1)


async def test_entered_renewed_confirmed_new_different_start_date_asks_conflict(tree):
    tree["policy_json"] = fresh_doc()
    started = await _entered_renewed_document_new(tree)
    await tree["answer"](started, {"policy_new_or_renewed_conflict": "New policy (first year)"})
    conflict = await tree["answer"](started, {"policy_new_start_date": "15-Mar-2026"})
    [q] = conflict["questions"]
    assert q["id"] == "policy_start_date_conflict"
    assert q["options"] == ["Use my entered date (15-03-2026)", "Use policy document date (01-04-2026)"]

    done = await tree["answer"](started, {"policy_start_date_conflict": q["options"][1]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


async def test_entered_renewed_confirmed_renewed_keeps_upload_answers(tree):
    tree["policy_json"] = fresh_doc()
    started = await _entered_renewed_document_new(tree)
    done = await tree["answer"](started, {"policy_new_or_renewed_conflict": "Renewed policy"})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2022-04-01"
    saved = await Policy.get(tree["policy"].id)
    assert saved.is_renewal is True and saved.continuous_renewal is True


async def test_document_silent_about_new_or_renewal_asks_nothing(tree):
    done = await tree["start"](datetime(2022, 4, 1), is_renewal=True, continuous=True)
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2022-04-01"


async def test_fresh_wording_with_a_previous_policy_is_not_new(tree):
    tree["policy_json"] = policy_json(isRenewal=False, renewalInformation="Fresh", previousPolicyNumber="OLD-1")
    done = await tree["start"](datetime(2022, 4, 1), is_renewal=True, continuous=True)
    assert done["status"] == "complete"


async def test_entered_new_and_document_new_asks_nothing(tree):
    tree["policy_json"] = fresh_doc()
    done = await tree["start"](datetime(2026, 4, 1), is_renewal=False)
    assert done["status"] == "complete"


# ── Entered Renewed at upload, checked against the document at the start ──────

async def test_upload_break_answer_against_document_saying_no_break(tree):
    tree["policy_json"] = no_break_statement()
    started = await tree["start"](datetime(2020, 4, 1), is_renewal=True, continuous=False)
    [q] = started["questions"]
    assert q["id"] == "policy_renewal_break_conflict"
    done = await tree["answer"](started, {"policy_renewal_break_conflict": q["options"][1]})
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2020-04-01"


async def test_upload_later_first_start_than_previous_period_is_asked(tree):
    tree["policy_json"] = renewal_doc()
    started = await tree["start"](datetime(2026, 4, 1), is_renewal=True, continuous=True)
    [q] = started["questions"]
    assert q["id"] == "policy_inception_date_conflict"
    assert "cover since 01 Apr 2025" in q["question"]


async def test_upload_earlier_first_start_than_previous_period_is_not_asked(tree):
    tree["policy_json"] = renewal_doc()
    done = await tree["start"](datetime(2022, 4, 1), is_renewal=True, continuous=True)
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == "2022-04-01"


async def test_upload_break_with_no_document_statement_asks_nothing(tree):
    tree["policy_json"] = renewal_doc()
    done = await tree["start"](datetime(2026, 4, 1), is_renewal=True, continuous=False)
    assert done["status"] == "complete"
    assert tree["analyzed_policy_json"]["waitingPeriodStartDate"] == CURRENT_START


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
