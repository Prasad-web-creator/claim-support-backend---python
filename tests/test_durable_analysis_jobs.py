import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.models.analysis_audit_log import AnalysisAuditLog
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession
from app.models.multi_policy_session import MultiPolicyAnalysisSession, PolicyAnalysisEntry
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services import analysis_jobs as jobs
from app.services import multi_policy_orchestrator as multi
from app.services.analysis_persistence import finish_analysis_write


@pytest.fixture
def queue_db(db, monkeypatch):
    monkeypatch.setattr(jobs, "get_database", lambda: db)
    monkeypatch.setattr(jobs, "_required", lambda: True)
    return db


@pytest.mark.asyncio
async def test_consent_is_versioned_owned_and_idempotent(queue_db):
    with pytest.raises(HTTPException) as missing:
        await jobs.require_analysis_consent("owner", require_job=True)
    assert missing.value.status_code == 403
    with pytest.raises(HTTPException) as refused:
        await jobs.create_job("owner", jobs._consent_version(), False)
    assert refused.value.status_code == 400

    created = await jobs.create_job("owner", jobs._consent_version(), True, "request-1")
    repeated = await jobs.create_job("owner", jobs._consent_version(), True, "request-1")
    assert created["id"] == repeated["id"]
    record = await jobs.require_job_consent("owner", created["id"])
    assert record["consent"]["accepted"] is True
    assert record["consent"]["acceptedAt"]
    with pytest.raises(HTTPException) as wrong_user:
        await jobs.require_job_consent("other", created["id"])
    assert wrong_user.value.status_code == 404

    await queue_db.analysis_jobs.update_one(
        {"_id": ObjectId(created["id"])}, {"$set": {"expiresAt": jobs._utcnow() - timedelta(seconds=1)}},
    )
    assert (await jobs.get_job("owner", created["id"]))["status"] == "expired"
    assert await queue_db.analysis_jobs.count_documents({"userId": "owner", "consent.accepted": True}) == 1


@pytest.mark.asyncio
async def test_start_rejects_cross_user_policy_and_is_idempotent(queue_db):
    rx = await Prescription(user_id="owner", is_manual=True).insert()
    mine = await Policy(user_id="owner").insert()
    other = await Policy(user_id="other").insert()
    job = await jobs.create_job("owner", jobs._consent_version(), True)
    with pytest.raises(HTTPException) as unauthorized:
        await jobs.enqueue_job("owner", job["id"], str(rx.id), [str(other.id)])
    assert unauthorized.value.status_code == 404
    initial = await jobs.enqueue_job("owner", job["id"], str(rx.id), [str(mine.id)])
    repeated = await jobs.enqueue_job("owner", job["id"], str(rx.id), [str(mine.id)])
    assert initial["status"] == repeated["status"] == "queued"
    assert await queue_db.analysis_jobs.count_documents({"status": "queued"}) == 1


@pytest.mark.asyncio
async def test_restart_recovers_expired_lease_without_duplicate_claim(queue_db, monkeypatch):
    job = await jobs.create_job("owner", jobs._consent_version(), True)
    oid = ObjectId(job["id"])
    await queue_db.analysis_jobs.update_one({"_id": oid}, {"$set": {"status": "queued"}})
    first = await jobs._claim_job()
    assert first["_id"] == oid
    assert await jobs._claim_job() is None
    await queue_db.analysis_jobs.update_one(
        {"_id": oid}, {"$set": {"status": "analyzing", "leaseUntil": jobs._utcnow() - timedelta(seconds=1)}},
    )
    resumed = await jobs._claim_job()
    assert resumed["_id"] == oid and resumed["leaseToken"] != first["leaseToken"]
    monkeypatch.setattr(jobs, "_process_job", AsyncMock(return_value={"status": "completed"}))
    await jobs._run_claimed(resumed)
    assert (await jobs.get_job("owner", job["id"]))["status"] == "completed"
    assert await jobs._claim_job() is None
    with pytest.raises(HTTPException) as completed:
        await jobs.cancel_job("owner", job["id"])
    assert completed.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_active_job_drains_writes_and_removes_only_analysis_data(queue_db, monkeypatch):
    rx = await Prescription(user_id="owner", is_manual=True).insert()
    policy = await Policy(user_id="owner").insert()
    job = await jobs.create_job("owner", jobs._consent_version(), True)
    oid = ObjectId(job["id"])
    parent = await MultiPolicyAnalysisSession(id=oid, user_id="owner", durable_job=True).insert()
    unrelated = await AnalysisReport(user_id="owner", status="completed").insert()
    child = await AnalysisSession(
        user_id="owner", parent_session_id=job["id"], policy_id=str(policy.id), prescription_id=str(rx.id),
    ).insert()
    await AnalysisAuditLog(session_id=str(child.id), analysis_round=1).insert()
    await jobs.enqueue_job("owner", job["id"], str(rx.id), [str(policy.id)])

    writing = asyncio.Event()
    finish_write = asyncio.Event()

    async def write_report():
        writing.set()
        await finish_write.wait()
        await AnalysisReport(
            user_id="owner", parent_session_id=job["id"], session_id=str(child.id), status="completed",
        ).insert()

    async def process(_job):
        await finish_analysis_write(write_report())
        pytest.fail("Analysis continued after cancellation")

    monkeypatch.setattr(jobs, "_process_job", process)
    worker = asyncio.create_task(jobs._run_claimed(await jobs._claim_job()))
    try:
        await asyncio.wait_for(writing.wait(), timeout=2)
        with pytest.raises(HTTPException) as denied:
            await jobs.cancel_job("other", job["id"])
        assert denied.value.status_code == 404
        assert (await jobs.cancel_job("owner", job["id"]))["status"] == "cancelling"
        assert await jobs._claim_job() is None
        assert await MultiPolicyAnalysisSession.get(parent.id) is not None
    finally:
        finish_write.set()
        await asyncio.wait_for(worker, timeout=2)

    assert await queue_db.analysis_jobs.find_one({"_id": oid}) is None
    assert await MultiPolicyAnalysisSession.get(parent.id) is None
    assert await AnalysisSession.get(child.id) is None
    assert await AnalysisAuditLog.find({"sessionId": str(child.id)}).count() == 0
    assert await AnalysisReport.find({"parentSessionId": job["id"]}).count() == 0
    assert await AnalysisReport.get(unrelated.id) is not None
    assert await Prescription.get(rx.id) is not None
    assert await Policy.get(policy.id) is not None


@pytest.mark.asyncio
async def test_validated_prescription_is_reused_after_policy_selection(queue_db, monkeypatch):
    text = "Diagnosis: Migraine\nVisit Date: 2025-01-20"
    rx = await Prescription(user_id="owner", is_manual=True, extracted_prescription_text=text).insert()
    policy = await Policy(user_id="owner").insert()
    job = await jobs.create_job("owner", jobs._consent_version(), True)
    extractor = AsyncMock(return_value={"extractedJson": {"diagnosis": "Migraine", "visitDate": "2025-01-20"}})
    monkeypatch.setattr(jobs, "extract_prescription_details", extractor)

    await jobs.enqueue_prescription("owner", job["id"], str(rx.id))
    await jobs._run_claimed(await jobs._claim_job())
    prepared = await jobs.get_job("owner", job["id"])
    assert prepared["stage"] == "prescription_ready" and prepared["prescriptionValid"] is True

    async def use_prepared_snapshot(parent, user_id):
        assert parent.prescription_stage == "ready"
        assert parent.prescription_text == text
        assert parent.prescription_json["diagnosis"] == "Migraine"
        # Self-entered prescriptions are checked as "if treated today" (commit 8d0a2b3 rule).
        from datetime import datetime
        assert parent.prescription_json["visitDate"] == datetime.now().strftime("%Y-%m-%d")
        return {"status": "completed", "policyAnalyses": [{"status": "completed"}]}

    monkeypatch.setattr(jobs, "run_multi_policy_analysis", use_prepared_snapshot)
    await jobs.enqueue_job("owner", job["id"], str(rx.id), [str(policy.id)])
    await jobs._run_claimed(await jobs._claim_job())
    assert (await jobs.get_job("owner", job["id"]))["status"] == "completed"
    extractor.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_reuses_persisted_report_and_preserves_sibling(queue_db):
    parent = await MultiPolicyAnalysisSession(user_id="owner", durable_job=True, policy_analyses=[
        PolicyAnalysisEntry(policy_id="one", status="analyzing"),
        PolicyAnalysisEntry(policy_id="two", status="waiting_for_user"),
    ]).insert()
    child_id = multi._durable_child_id(parent, "one")
    await AnalysisReport(
        id=child_id, user_id="owner", policy_id="one", session_id=str(child_id),
        overall_status="Covered", status="completed",
    ).insert()
    assert await multi._recover_durable_entry(parent, parent.policy_analyses[0])
    recovered = await MultiPolicyAnalysisSession.get(parent.id)
    assert [entry.status for entry in recovered.policy_analyses] == ["completed", "waiting_for_user"]
    assert recovered.policy_analyses[0].report_id == str(child_id)


@pytest.mark.asyncio
async def test_answer_queue_persists_only_questions_for_selected_policy(queue_db, monkeypatch):
    job = await jobs.create_job("owner", jobs._consent_version(), True)
    oid = ObjectId(job["id"])
    await MultiPolicyAnalysisSession(id=oid, user_id="owner", durable_job=True, policy_analyses=[
        PolicyAnalysisEntry(policy_id="one", status="waiting_for_user", questions=[{"id": "date"}]),
        PolicyAnalysisEntry(policy_id="two", status="waiting_for_user", questions=[{"id": "member"}]),
    ]).insert()
    await queue_db.analysis_jobs.update_one({"_id": oid}, {"$set": {
        "status": "waiting_for_user", "inputs": {"prescriptionPath": "rx", "policyIds": ["one", "two"]},
    }})
    with pytest.raises(HTTPException) as incorrect_question:
        await jobs.enqueue_answer("owner", job["id"], "one", {"member": True})
    assert incorrect_question.value.status_code == 422
    result = await jobs.enqueue_answer("owner", job["id"], "one", {"date": "2025-01-20"})
    assert result["status"] == "reanalyzing"
    saved = await queue_db.analysis_jobs.find_one({"_id": oid})
    assert saved["pendingAnswer"] == {"policyId": "one", "answers": {"date": "2025-01-20"}}
    monkeypatch.setattr(jobs, "_process_job", AsyncMock(return_value={"status": "completed"}))
    await jobs._run_claimed(await jobs._claim_job())
    repeated = await jobs.enqueue_answer("owner", job["id"], "one", {"date": "2025-01-20"})
    assert repeated["status"] == "completed"
    assert await jobs._claim_job() is None
