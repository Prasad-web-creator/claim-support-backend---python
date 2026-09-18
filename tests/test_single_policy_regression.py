"""
Single-policy regression tests.

`start_analysis_session` was refactored to share its core with the multi-policy
orchestrator. These tests pin the behaviour that must not change: report
persistence, extraction caching, the policy start date conflict question, the
invalid-document path, manual prescriptions and the clarification resume.

Only the expensive leaves (file fetch, OCR, LLM) are stubbed; the session,
report, rule engine and report generator all run for real.
"""

from datetime import datetime

import pytest
import pytest_asyncio

from tests.conftest import VALID_POLICY_JSON, VALID_RX_JSON

import app.services.analysis_interactive_orchestrator as aio
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession
from app.models.policy import Policy
from app.models.prescription import Prescription

USER = "user-1"


class SingleHarness:
    def __init__(self):
        self.doc_extractions = []
        self.policy_json_extractions = 0
        self.rx_json_extractions = 0
        self.coverage_calls = []
        self.coverage_result = {
            "next_action": "generate_report",
            "confidence": 90,
            "analysis": {
                "overallStatus": "Covered",
                "summary": "Covered under the policy.",
                "comparison": [
                    {"item": "Appendectomy", "coverageStatus": "Covered", "cost": 50000}
                ],
            },
        }


@pytest_asyncio.fixture
async def single(monkeypatch, db):
    h = SingleHarness()

    async def fake_process_document(doc_id, user_id, doc_label="Document"):
        h.doc_extractions.append(doc_label)
        return f"{doc_label} TEXT"

    monkeypatch.setattr(aio, "_process_document", fake_process_document)

    async def fake_extract_policy(text):
        h.policy_json_extractions += 1
        return {"extractedJson": dict(VALID_POLICY_JSON)}

    monkeypatch.setattr(aio, "extract_policy_details", fake_extract_policy)

    async def fake_extract_rx(text):
        h.rx_json_extractions += 1
        return {"extractedJson": dict(VALID_RX_JSON)}

    monkeypatch.setattr(aio, "extract_prescription_details", fake_extract_rx)

    async def fake_analyze_coverage(**kwargs):
        h.coverage_calls.append(kwargs)
        return h.coverage_result

    monkeypatch.setattr(aio, "analyze_coverage", fake_analyze_coverage)

    async def fake_background(**kwargs):
        return None

    monkeypatch.setattr(aio, "_background_post_processing", fake_background)

    async def add_policy(cached=False, start_date=None, user=USER):
        policy = Policy(
            user_id=user,
            policy_name="Policy A",
            insurance_company="TestInsurer",
            grid_fs_file_id="file-policy",
            policy_start_date=start_date,
            extracted_policy_text="CACHED POLICY TEXT" if cached else "",
            extracted_policy_json=dict(VALID_POLICY_JSON) if cached else {},
        )
        await policy.insert()
        return policy

    async def add_prescription(is_manual=False):
        rx = Prescription(
            user_id=USER,
            is_manual=is_manual,
            grid_fs_file_id="" if is_manual else "file-rx",
            extracted_prescription_text="I have had severe abdominal pain" if is_manual else "",
        )
        await rx.insert()
        return rx

    h.add_policy = add_policy
    h.add_prescription = add_prescription
    return h


# ──────────────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────────────

async def test_single_policy_completes_and_persists_report(single):
    policy = await single.add_policy()
    rx = await single.add_prescription()

    result = await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    assert result["status"] == "complete"
    # The deterministic rule engine owns the decision; we only assert it ran and
    # produced one, not which way it went for this fixture data.
    assert result["overallStatus"]

    reports = await AnalysisReport.find(AnalysisReport.user_id == USER).to_list()
    assert len(reports) == 1
    report = reports[0]
    assert report.policy_id == str(policy.id)
    assert report.prescription_id == str(rx.id)
    assert report.status == "completed"
    assert report.decision_type == "Automatic"
    assert report.report_number == 1
    # Classic single-policy sessions carry no parent link
    assert report.parent_session_id == ""

    sessions = await AnalysisSession.find(AnalysisSession.user_id == USER).to_list()
    assert len(sessions) == 1
    assert sessions[0].status == "completed"
    assert sessions[0].report_id == str(report.id)
    assert sessions[0].parent_session_id == ""


async def test_uncached_policy_extracts_both_documents(single):
    policy = await single.add_policy(cached=False)
    rx = await single.add_prescription()

    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    assert sorted(single.doc_extractions) == ["Policy Document", "Prescription Document"]
    assert single.policy_json_extractions == 1
    assert single.rx_json_extractions == 1


async def test_cached_policy_skips_policy_extraction(single):
    """Existing cache behaviour: a cached policy skips OCR *and* policy LLM extraction."""
    policy = await single.add_policy(cached=True)
    rx = await single.add_prescription()

    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    assert single.doc_extractions == ["Prescription Document"]
    assert single.policy_json_extractions == 0
    assert single.rx_json_extractions == 1


async def test_policy_extraction_is_cached_for_next_run(single):
    policy = await single.add_policy(cached=False)
    rx = await single.add_prescription()

    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    refreshed = await Policy.get(policy.id)
    assert refreshed.extracted_policy_text == "Policy Document TEXT"
    assert refreshed.extracted_policy_json.get("insuranceCompany") == "TestInsurer"


async def test_prescription_extraction_is_cached_for_next_run(single):
    policy = await single.add_policy()
    rx = await single.add_prescription()

    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    refreshed = await Prescription.get(rx.id)
    assert refreshed.extracted_prescription_text == "Prescription Document TEXT"
    assert refreshed.extracted_prescription_json.get("diagnosis") == "Acute appendicitis"
    assert refreshed.diagnosis == "Acute appendicitis"
    assert refreshed.processing_status == "completed"


# ──────────────────────────────────────────────────────────────────────────────
# Authorization
# ──────────────────────────────────────────────────────────────────────────────

async def test_cannot_analyze_another_users_policy(single):
    policy = await single.add_policy(user="someone-else")
    rx = await single.add_prescription()

    with pytest.raises(ValueError, match="Invalid Policy ID or unauthorized"):
        await aio.start_analysis_session(
            user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
        )


# ──────────────────────────────────────────────────────────────────────────────
# Clarification
# ──────────────────────────────────────────────────────────────────────────────

async def test_policy_start_date_conflict_raises_question(single):
    policy = await single.add_policy(start_date=datetime(2024, 1, 15))
    rx = await single.add_prescription()

    # Document says a different start date than the one the user entered
    policy_json = dict(VALID_POLICY_JSON, policyStartDate="2023-05-01")

    async def conflicting_policy(text):
        return {"extractedJson": policy_json}

    aio.extract_policy_details = conflicting_policy  # module-level rebind for this test

    try:
        result = await aio.start_analysis_session(
            user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
        )
    finally:
        pass

    assert result["status"] == "needs_clarification"
    assert result["questions"][0]["id"] == "policy_start_date_conflict"
    assert "15-01-2024" in result["questions"][0]["question"]

    session = await AnalysisSession.get(result["sessionId"])
    assert session.status == "waiting_for_user"
    assert len(session.questions) == 1

    # No report is written while waiting for the user
    assert await AnalysisReport.find(AnalysisReport.user_id == USER).count() == 0


async def test_llm_clarification_then_resume_completes(single):
    policy = await single.add_policy()
    rx = await single.add_prescription()

    single.coverage_result = {
        "next_action": "ask_questions",
        "confidence": 40,
        "questions": [{
            "id": "hospitalization_status",
            "title": "Hospitalization",
            "question": "Were you admitted?",
            "type": "single_choice",
            "options": ["Yes, admitted", "No, OPD only"],
        }],
    }

    started = await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )
    assert started["status"] == "needs_clarification"
    session_id = started["sessionId"]

    # Answer it — the pipeline resumes without re-extracting anything
    single.coverage_result = {
        "next_action": "generate_report",
        "confidence": 95,
        "analysis": {"overallStatus": "Covered", "summary": "OK", "comparison": []},
    }
    docs_before = list(single.doc_extractions)

    resumed = await aio.resume_analysis_session(
        session_id=session_id, user_id=USER, answers={"hospitalization_status": "Yes, admitted"}
    )

    assert resumed["status"] == "complete"
    assert single.doc_extractions == docs_before          # no re-extraction on resume

    session = await AnalysisSession.get(session_id)
    assert session.status == "completed"
    assert session.round_count == 2
    assert len(session.answers) == 1
    assert session.prescription_json.get("hospitalizationRequired") is True


async def test_resume_rejects_session_not_waiting(single):
    policy = await single.add_policy()
    rx = await single.add_prescription()
    result = await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    session_id = str((await AnalysisSession.find(AnalysisSession.user_id == USER).to_list())[0].id)
    with pytest.raises(ValueError, match="not waiting for user input"):
        await aio.resume_analysis_session(session_id=session_id, user_id=USER, answers={})


# ──────────────────────────────────────────────────────────────────────────────
# Invalid documents
# ──────────────────────────────────────────────────────────────────────────────

async def test_invalid_prescription_report_is_not_persisted(single, monkeypatch):
    policy = await single.add_policy()
    rx = await single.add_prescription()

    async def empty_rx(text):
        return {"extractedJson": {"diagnosis": ""}}

    monkeypatch.setattr(aio, "extract_prescription_details", empty_rx)

    result = await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    assert result["overallStatus"] == "Invalid Prescription"
    # Existing rule: invalid reports are returned but never stored
    assert await AnalysisReport.find(AnalysisReport.user_id == USER).count() == 0

    session = (await AnalysisSession.find(AnalysisSession.user_id == USER).to_list())[0]
    assert session.status == "completed"


# ──────────────────────────────────────────────────────────────────────────────
# Manual prescriptions
# ──────────────────────────────────────────────────────────────────────────────

async def test_manual_prescription_skips_file_extraction(single):
    policy = await single.add_policy()
    rx = await single.add_prescription(is_manual=True)

    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    # Manual text is used directly — only the policy document is fetched
    assert single.doc_extractions == ["Policy Document"]

    refreshed = await Prescription.get(rx.id)
    assert refreshed.is_manual is True
    assert refreshed.extracted_prescription_json["isManual"] is True
    assert refreshed.extracted_prescription_json["prescriptionSource"] == "Self-entered Prescription"


async def test_manual_prescription_defaults_visit_date_to_today(single, monkeypatch):
    policy = await single.add_policy()
    rx = await single.add_prescription(is_manual=True)

    async def rx_without_date(text):
        return {"extractedJson": {"diagnosis": "Fever", "medicines": [{"name": "Paracetamol"}]}}

    monkeypatch.setattr(aio, "extract_prescription_details", rx_without_date)

    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    refreshed = await Prescription.get(rx.id)
    today = datetime.now().strftime("%Y-%m-%d")
    assert refreshed.extracted_prescription_json["visitDate"] == today
    assert refreshed.extracted_prescription_json["consultationDate"] == today


# ──────────────────────────────────────────────────────────────────────────────
# Failure handling
# ──────────────────────────────────────────────────────────────────────────────

async def test_failure_marks_session_failed_and_raises(single, monkeypatch):
    policy = await single.add_policy()
    rx = await single.add_prescription()

    async def boom(**kwargs):
        raise RuntimeError("gemini unavailable")

    monkeypatch.setattr(aio, "analyze_coverage", boom)

    with pytest.raises(RuntimeError, match="gemini unavailable"):
        await aio.start_analysis_session(
            user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
        )

    session = (await AnalysisSession.find(AnalysisSession.user_id == USER).to_list())[0]
    assert session.status == "failed"


async def test_manual_review_is_persisted_as_report(single):
    policy = await single.add_policy()
    rx = await single.add_prescription()

    single.coverage_result = {
        "next_action": "manual_review",
        "confidence": 30,
        "reason": "Evidence insufficient",
        "analysis": {"overallStatus": "Manual Review", "summary": "Needs a human", "comparison": []},
    }

    result = await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    assert result["status"] == "manual_review_required"
    reports = await AnalysisReport.find(AnalysisReport.user_id == USER).to_list()
    assert len(reports) == 1
    assert reports[0].decision_type == "Manual Review"
    assert reports[0].status == "manual_review_required"


async def test_background_metadata_flag_defaults_to_full_extraction(single, monkeypatch):
    """Single-policy runs keep extracting both policy and prescription metadata."""
    import app.services.analysis_orchestrator as ao
    import inspect

    sig = inspect.signature(ao._background_post_processing)
    assert sig.parameters["skip_prescription_metadata"].default is False

    calls = {}

    async def fake_background(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(aio, "_background_post_processing", fake_background)

    policy = await single.add_policy()
    rx = await single.add_prescription()
    await aio.start_analysis_session(
        user_id=USER, prescription_id=str(rx.id), policy_doc_id=str(policy.id)
    )

    # A classic single-policy session has no parent, so nothing is skipped
    assert calls["skip_prescription_metadata"] is False
