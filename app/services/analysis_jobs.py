"""Versioned consent and durable analysis work backed by MongoDB."""

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from app.core.language import preferred_language
import json
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import AutoReconnect

from app.core.config import get_settings
from app.core.database import get_database
from app.core.logging import logger
from app.models.analysis_audit_log import AnalysisAuditLog
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession
from app.models.multi_policy_session import MultiPolicyAnalysisSession
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services.analysis_interactive_orchestrator import (
    _add_manual_prescription_context, _load_prescription_text, _persist_prescription_extraction,
    _resolve_prescription_context,
)
from app.services.extraction.json_validation import validate_extracted_json
from app.services.extraction.prescription_extraction import extract_prescription_details
from app.services.multi_policy_orchestrator import (
    create_multi_policy_session,
    resume_durable_policy_analysis,
    retry_durable_policy_analysis,
    run_multi_policy_analysis,
    serialize_multi_session,
)
from app.services.storage.file_upload_service import FileUploadService
from app.utils.file_validators import PRESCRIPTION_MIME_TYPES


QUEUED = ("queued", "reanalyzing")
ACTIVE = ("extracting_documents", "extracting_policy_metadata", "analyzing", "reanalyzing")
LIVE = ("created", "queued", "waiting_for_user", *ACTIVE)
LEASE_DURATION = timedelta(seconds=60)
_new_work: asyncio.Event | None = None
_running: dict[ObjectId, asyncio.Task] = {}


def _notify_worker() -> None:
    if _new_work is not None:
        _new_work.set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _jobs():
    return get_database()["analysis_jobs"]


async def initialize_analysis_jobs() -> None:
    collection = _jobs()
    await collection.create_index([("status", ASCENDING), ("leaseUntil", ASCENDING), ("updatedAt", ASCENDING)])
    await collection.create_index([("userId", ASCENDING), ("createdAt", DESCENDING)])
    await collection.create_index([("status", ASCENDING), ("expiresAt", ASCENDING)])


def _required() -> bool:
    return get_settings().REQUIRE_ANALYSIS_CONSENT


def _consent_version() -> str:
    return str(get_settings().ANALYSIS_AGREEMENT_VERSION)


def _expiry(now: datetime) -> datetime:
    return now + timedelta(hours=get_settings().ANALYSIS_JOB_EXPIRY_HOURS)


async def _find_owned(user_id: str, job_id: str) -> dict:
    if not ObjectId.is_valid(job_id):
        raise HTTPException(404, "Analysis job not found.")
    job = await _jobs().find_one({"_id": ObjectId(job_id), "userId": user_id})
    if job is None:
        raise HTTPException(404, "Analysis job not found.")
    return job


async def require_job_consent(user_id: str, job_id: str, *, allow_terminal: bool = False) -> dict:
    job = await _find_owned(user_id, job_id)
    consent = job.get("consent", {})
    expiry = job["expiresAt"]
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if (
        consent.get("accepted") is not True
        or consent.get("version") != _consent_version()
        or job["status"] not in (LIVE + ("completed", "failed", "manual_review_required") if allow_terminal else LIVE)
        or expiry <= _utcnow()
    ):
        raise HTTPException(403, "Accept the current analysis agreement to continue.")
    return job


async def require_analysis_consent(user_id: str, job_id: str | None = None, *, require_job: bool = False) -> None:
    if job_id:
        await require_job_consent(user_id, job_id)
        return
    if not _required():
        return
    if not require_job:
        exists = await _jobs().find_one({
            "userId": user_id,
            "consent.version": _consent_version(),
            "consent.accepted": True,
            "status": {"$in": LIVE},
            "expiresAt": {"$gt": _utcnow()},
        }, {"_id": 1})
        if exists:
            return
    raise HTTPException(403, "Accept the analysis agreement before processing documents.")


async def _expire_inactive(scope: dict | None = None) -> None:
    now = _utcnow()
    await _jobs().update_many({
        **(scope or {}), "status": {"$in": LIVE}, "expiresAt": {"$lte": now},
        "$or": [{"leaseUntil": None}, {"leaseUntil": {"$lte": now}}],
    }, {"$set": {"status": "expired", "updatedAt": now}, "$unset": {
        "pendingAnswer": "", "pendingRetry": "", "pendingPrescription": "", "leaseToken": "", "leaseUntil": "",
    }})


async def create_job(user_id: str, version: str, accepted: bool, request_id: str | None = None) -> dict:
    if accepted is not True or version != _consent_version():
        raise HTTPException(400, "Accept the current analysis agreement to continue.")
    now = _utcnow()
    job = {
        "_id": ObjectId(), "userId": user_id,
        "status": "created", "createdAt": now, "updatedAt": now, "expiresAt": _expiry(now),
        "consent": {"accepted": True, "version": version, "acceptedAt": now},
    }
    if request_id:
        job["_id"] = ObjectId(sha256(f"{user_id}:{request_id}".encode()).digest()[:12])
        await _jobs().update_one({"_id": job["_id"]}, {"$setOnInsert": job}, upsert=True)
        job = await _find_owned(user_id, str(job["_id"]))
    else:
        await _jobs().insert_one(job)
    return await serialize_job(job)


async def enqueue_job(user_id: str, job_id: str, prescription_id: str, policy_ids: list[str]) -> dict:
    job = await _find_owned(user_id, job_id)
    requested = {"prescriptionPath": prescription_id, "policyIds": policy_ids}
    if job.get("inputs") == requested:
        return await serialize_job(job)
    await require_job_consent(user_id, job_id)
    if job["status"] != "created":
        raise HTTPException(409, "This analysis job has already started.")
    if job.get("preparedPrescription") == prescription_id and job.get("prescriptionValid") is False:
        raise HTTPException(422, "Provide a readable prescription or medical details before starting analysis.")
    await _check_prescription_owner(user_id, prescription_id)
    for policy_id in policy_ids:
        policy = await Policy.get(ObjectId(policy_id)) if ObjectId.is_valid(policy_id) else None
        if not policy or policy.user_id != user_id:
            raise HTTPException(404, "Policy not found.")
    updated = await _jobs().find_one_and_update(
        {"_id": job["_id"], "status": "created", "expiresAt": {"$gt": _utcnow()}},
        {"$set": {"status": "queued", "inputs": requested, "updatedAt": _utcnow(),
                  "responseLanguage": await preferred_language(user_id)}},
        return_document=ReturnDocument.AFTER,
    )
    final_job = updated or await _find_owned(user_id, job_id)
    if final_job.get("inputs") != requested:
        raise HTTPException(409, "This analysis job has already started.")
    if updated is not None:
        _notify_worker()
    return await serialize_job(final_job)


async def _check_prescription_owner(user_id: str, prescription_id: str):
    if not ObjectId.is_valid(prescription_id):
        raise HTTPException(422, "Save a prescription before starting an analysis.")
    prescription = await Prescription.get(ObjectId(prescription_id))
    if prescription is not None and prescription.user_id != user_id:
        raise HTTPException(404, "Prescription not found.")
    if prescription is None:
        owned_file = await FileUploadService.get_owned_file(prescription_id, user_id)
        if owned_file.mime_type not in PRESCRIPTION_MIME_TYPES:
            raise HTTPException(422, "Upload a prescription as PDF, JPG or PNG.")


async def enqueue_prescription(user_id: str, job_id: str, prescription_id: str) -> dict:
    job = await require_job_consent(user_id, job_id)
    if job.get("pendingPrescription") == prescription_id or job.get("preparedPrescription") == prescription_id:
        return await serialize_job(job)
    if job["status"] != "created":
        raise HTTPException(409, "Wait until the current prescription has finished processing.")
    await _check_prescription_owner(user_id, prescription_id)
    updated = await _jobs().find_one_and_update(
        {"_id": job["_id"], "status": "created", "expiresAt": {"$gt": _utcnow()}},
        {"$set": {"status": "queued", "pendingPrescription": prescription_id, "updatedAt": _utcnow()},
         "$unset": {"error": "", "preparedPrescription": "", "prescriptionValid": ""}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        updated = await _find_owned(user_id, job_id)
        if updated.get("pendingPrescription") != prescription_id:
            raise HTTPException(409, "This job is already processing a prescription.")
    else:
        _notify_worker()
    return await serialize_job(updated)


async def serialize_job(job: dict) -> dict:
    status = job["status"]
    inputs = job.get("inputs")
    result = None
    if job.get("inputs") and status not in ("created", "cancelling", "cancelled", "expired"):
        parent = await MultiPolicyAnalysisSession.get(job["_id"])
        if parent and parent.user_id == job["userId"]:
            result = serialize_multi_session(parent, include_prescription_json=False)
            if status in ACTIVE and parent.prescription_stage == "ready" and status != "reanalyzing":
                reading_policies = any(entry.stage == "policy_extraction" for entry in parent.policy_analyses)
                status = "extracting_policy_metadata" if reading_policies else "analyzing"
    return {
        "id": str(job["_id"]), "status": status,
        "policyIds": inputs.get("policyIds", []) if isinstance(inputs, dict) else [],
        "activePolicyId": (job.get("pendingAnswer") or {}).get("policyId") or job.get("pendingRetry"),
        "stage": (
            "prescription_ready" if status == "created" and job.get("prescriptionValid") is True
            else "invalid_prescription" if status == "created" and job.get("prescriptionValid") is False
            else "consent" if status == "created" else status
        ),
        "result": result, "error": job.get("error"),
        "prescriptionValid": job.get("prescriptionValid"),
        "agreementVersion": job["consent"]["version"],
        "createdAt": job["createdAt"], "expiresAt": job["expiresAt"],
    }


async def get_job(user_id: str, job_id: str) -> dict:
    if ObjectId.is_valid(job_id):
        await _expire_inactive({"_id": ObjectId(job_id), "userId": user_id})
    return await serialize_job(await _find_owned(user_id, job_id))


async def list_jobs(user_id: str) -> list[dict]:
    await _expire_inactive({"userId": user_id})
    jobs = await _jobs().find({"userId": user_id}).sort("createdAt", DESCENDING).limit(20).to_list(length=20)
    return [await serialize_job(job) for job in jobs]


async def cancel_job(user_id: str, job_id: str) -> dict:
    job = await _find_owned(user_id, job_id)
    if job["status"] == "cancelled":  # Jobs cancelled by older versions.
        return await serialize_job(job)
    updated = await _jobs().find_one_and_update(
        {"_id": job["_id"], "userId": user_id, "status": {"$in": LIVE}},
        {"$set": {"status": "cancelling", "updatedAt": _utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        updated = await _find_owned(user_id, job_id)
        if updated["status"] != "cancelling":
            raise HTTPException(409, "This analysis has already finished.")
    elif (work := _running.get(job["_id"])) is not None:
        work.cancel()

    _notify_worker()
    if await _finish_cancellation(updated):
        return await serialize_job({**updated, "status": "cancelled", "inputs": None, "error": None})
    return await serialize_job(updated)


def _cancellation_ready() -> dict:
    return {
        "status": "cancelling",
        "$or": [{"leaseUntil": None}, {"leaseUntil": {"$lte": _utcnow()}}],
    }


async def _finish_cancellation(job: dict) -> bool:
    # A live worker owns all writes for this job until it has drained its tasks.
    selector = {"_id": job["_id"], "userId": job["userId"], **_cancellation_ready()}
    if await _jobs().find_one(selector, {"_id": 1}) is None:
        return False
    owned = {"userId": job["userId"], "parentSessionId": str(job["_id"])}
    children = await AnalysisSession.find(owned).to_list()
    child_ids = [str(child.id) for child in children]
    if child_ids:
        await AnalysisAuditLog.find({"sessionId": {"$in": child_ids}}).delete()
    await AnalysisReport.find({
        "userId": job["userId"],
        "$or": [{"parentSessionId": str(job["_id"])}, {"sessionId": {"$in": child_ids}}],
    }).delete()
    await AnalysisSession.find(owned).delete()
    await MultiPolicyAnalysisSession.find({"_id": job["_id"], "userId": job["userId"]}).delete()
    await _jobs().delete_one(selector)
    return True


def _answer_fingerprint(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def enqueue_answer(user_id: str, job_id: str, policy_id: str | None, answers: dict) -> dict:
    job = await require_job_consent(user_id, job_id, allow_terminal=True)
    parent = await MultiPolicyAnalysisSession.get(job["_id"])
    if parent and not policy_id and len(parent.policy_analyses) == 1:
        policy_id = parent.policy_analyses[0].policy_id
    pending = {"policyId": policy_id, "answers": answers}
    if job.get("pendingAnswer") == pending or job.get("lastAnswerFingerprint") == _answer_fingerprint(pending):
        return await serialize_job(job)
    entry = parent.get_entry(policy_id) if parent and parent.user_id == user_id and policy_id else None
    if job["status"] != "waiting_for_user" or not entry or entry.status != "waiting_for_user":
        raise HTTPException(409, "This policy is not waiting for clarification.")
    allowed = {question["id"] for question in entry.questions if question.get("id")}
    required = {question["id"] for question in entry.questions if question.get("id") and question.get("required", True)}
    if not answers or not answers.keys() <= allowed:
        raise HTTPException(422, "Answer only the questions shown for this policy.")
    if not required <= answers.keys() or any(
        answers[key] is None or answers[key] == "" or answers[key] == [] for key in required
    ):
        raise HTTPException(422, "Answer all required clarification questions.")
    updated = await _jobs().find_one_and_update(
        {"_id": job["_id"], "status": "waiting_for_user"},
        {"$set": {"status": "reanalyzing", "pendingAnswer": pending, "updatedAt": _utcnow(),
                  "operationLanguage": await preferred_language(user_id)}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "This clarification has already been submitted.")
    _notify_worker()
    return await serialize_job(updated)


async def enqueue_retry(user_id: str, job_id: str, policy_id: str) -> dict:
    job = await require_job_consent(user_id, job_id, allow_terminal=True)
    if job.get("pendingRetry") == policy_id:
        return await serialize_job(job)
    parent = await MultiPolicyAnalysisSession.get(job["_id"])
    entry = parent.get_entry(policy_id) if parent and parent.user_id == user_id else None
    if job["status"] not in ("failed", "completed", "waiting_for_user") or not entry or entry.status != "failed":
        raise HTTPException(409, "Only a failed policy analysis can be retried.")
    updated = await _jobs().find_one_and_update(
        {"_id": job["_id"], "status": job["status"], "leaseToken": None},
        {"$set": {"status": "reanalyzing", "pendingRetry": policy_id, "updatedAt": _utcnow(),
                  "operationLanguage": await preferred_language(user_id)},
         "$unset": {"error": ""}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "This analysis already has work pending.")
    _notify_worker()
    return await serialize_job(updated)


async def _claim_job() -> dict | None:
    now = _utcnow()
    return await _jobs().find_one_and_update({
        "status": {"$in": (*QUEUED, *ACTIVE)}, "expiresAt": {"$gt": now},
        "$or": [{"leaseUntil": None}, {"leaseUntil": {"$lte": now}}],
    }, {"$set": {
        "leaseToken": uuid4().hex, "leaseUntil": now + LEASE_DURATION,
        "updatedAt": now,
    }}, sort=[("updatedAt", ASCENDING)], return_document=ReturnDocument.AFTER)


def _job_status(result: dict) -> str:
    status = result["status"]
    if status == "completed" and all(
        row["status"] == "manual_review_required" for row in result["policyAnalyses"]
    ):
        return "manual_review_required"
    return "completed" if status == "partial" else status


async def _process_job(job: dict) -> dict:
    if job.get("pendingPrescription"):
        prescription_id = job["pendingPrescription"]
        rx = await _resolve_prescription_context(job["userId"], prescription_id)
        text = await _load_prescription_text(rx, job["userId"])
        extracted = (await extract_prescription_details(text)).get("extractedJson") or {}
        # Same order as the shared multi-policy extraction: validate, then tag manual input.
        validation = validate_extracted_json({}, extracted)
        prescription_json = validation["validatedPrescriptionJson"]
        if rx.is_manual:
            _add_manual_prescription_context(prescription_json, text)
        valid = validation["isPrescriptionValid"]
        if valid:
            await _persist_prescription_extraction(
                rx, user_id=job["userId"], rx_text=text,
                prescription_json=prescription_json,
                fallback_original_name="Prescription",
            )
        error = None if valid else "Upload a readable prescription or enter medical details manually."
        return {"status": "created", "preparedPrescription": prescription_id, "prescriptionValid": valid, "error": error}
    parent = await MultiPolicyAnalysisSession.get(job["_id"])
    if parent is not None and parent.user_id != job["userId"]:
        raise RuntimeError("Analysis ownership mismatch")
    if parent is None:
        inputs = job["inputs"]
        prescription_id = inputs["prescriptionPath"]
        prepared = None
        if job.get("prescriptionValid") is True and job.get("preparedPrescription") == prescription_id:
            rx = (await _resolve_prescription_context(job["userId"], prescription_id)).rx_doc
            if rx and rx.extracted_prescription_text and rx.extracted_prescription_json:
                prepared = rx
        parent = await create_multi_policy_session(
            job["userId"], prescription_id, inputs["policyIds"], durable_id=job["_id"], prepared_rx=prepared,
            response_language=job.get("responseLanguage", "en"),
        )
    pending = job.get("pendingAnswer")
    if pending:
        result = await resume_durable_policy_analysis(
            parent, pending["policyId"], pending["answers"], response_language=job.get("operationLanguage"),
        )
    elif job.get("pendingRetry"):
        result = await retry_durable_policy_analysis(
            parent, job["pendingRetry"], response_language=job.get("operationLanguage"),
        )
    else:
        result = await run_multi_policy_analysis(parent, job["userId"])
    return {"status": _job_status(result)}


async def _heartbeat(selector: dict, work: asyncio.Task) -> None:
    try:
        while not work.done():
            await asyncio.sleep(15)
            result = await _jobs().update_one(selector, {"$set": {"leaseUntil": _utcnow() + LEASE_DURATION}})
            if not result.matched_count:
                if not work.cancelling():
                    work.cancel()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        if not work.cancelling():
            work.cancel()
        raise


async def _release_job_lease(selector: dict) -> None:
    try:
        await _jobs().update_one(selector, {"$unset": {"leaseToken": "", "leaseUntil": ""}})
    except Exception as exc:
        logger.warning("Analysis lease will expire automatically ({})", type(exc).__name__)


async def _claim_for_worker() -> dict | None:
    claim = asyncio.create_task(_claim_job())
    try:
        return await asyncio.shield(claim)
    except asyncio.CancelledError:
        # Mongo may already have written the lease. Let the claim finish and release it.
        try:
            job = await claim
            if job is not None:
                await _release_job_lease({"_id": job["_id"], "leaseToken": job["leaseToken"]})
        except Exception as exc:
            logger.warning("Analysis claim interrupted ({})", type(exc).__name__)
        raise


async def _run_claimed(job: dict) -> None:
    selector = {"_id": job["_id"], "leaseToken": job["leaseToken"]}
    active = {**selector, "status": {"$ne": "cancelling"}}
    initial = "reanalyzing" if job.get("pendingAnswer") or job.get("pendingRetry") else "extracting_documents"
    work = heartbeat = None
    try:
        begun = await _jobs().update_one(active, {"$set": {"status": initial}})
        if not begun.matched_count:
            return
        work = asyncio.create_task(_process_job(job))
        _running[job["_id"]] = work
        heartbeat = asyncio.create_task(_heartbeat(active, work))
        fields = await asyncio.shield(work)
        fields["updatedAt"] = _utcnow()
        if job.get("pendingAnswer"):
            fields["lastAnswerFingerprint"] = _answer_fingerprint(job["pendingAnswer"])
        if fields["status"] == "waiting_for_user":
            fields["expiresAt"] = _expiry(_utcnow())
        await _jobs().update_one(active, {
            "$set": fields, "$unset": {
                "leaseToken": "", "leaseUntil": "", "pendingAnswer": "", "pendingRetry": "", "pendingPrescription": "",
            },
        })
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
    except AutoReconnect:
        raise
    except Exception as exc:
        logger.warning("Durable analysis failed ({})", type(exc).__name__)
        failed = {"status": "failed", "error": "Analysis could not be completed.", "updatedAt": _utcnow()}
        if job.get("pendingPrescription"):
            failed.update(status="created", preparedPrescription=job["pendingPrescription"], prescriptionValid=False,
                          error="Upload a readable prescription or enter medical details manually.")
        await _jobs().update_one(active, {
            "$set": failed,
            "$unset": {"leaseToken": "", "leaseUntil": "", "pendingAnswer": "", "pendingRetry": "", "pendingPrescription": ""},
        })
    finally:
        for task in (heartbeat, work):
            if task is not None and not task.done() and not task.cancelling():
                task.cancel()
        if work is not None and heartbeat is not None:
            await asyncio.gather(work, heartbeat, return_exceptions=True)
        if _running.get(job["_id"]) is work:
            _running.pop(job["_id"], None)
        await _release_job_lease(selector)
        await _finish_cancellation(job)


async def _runner(new_work: asyncio.Event) -> None:
    next_expiry = _utcnow()
    retry_delay = 3
    while True:
        # Clear before claiming so a concurrent enqueue can wake this wait.
        new_work.clear()
        try:
            if _utcnow() >= next_expiry:
                await _expire_inactive()
                next_expiry = _utcnow() + timedelta(minutes=1)
            cancellation = await _jobs().find_one(_cancellation_ready())
            if cancellation:
                await _finish_cancellation(cancellation)
                continue
            job = await _claim_for_worker()
            if job:
                await _run_claimed(job)
                retry_delay = 3
                continue
            retry_delay = 3
        except Exception as exc:
            logger.warning("Analysis queue temporarily unavailable ({})", type(exc).__name__)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
            continue
        try:
            async with asyncio.timeout(3):
                await new_work.wait()
        except TimeoutError:
            pass


def start_analysis_job_worker() -> asyncio.Task:
    global _new_work
    _new_work = asyncio.Event()
    return asyncio.create_task(_runner(_new_work), name="analysis-jobs")


async def stop_analysis_job_worker(task: asyncio.Task) -> None:
    global _new_work
    wake_event = _new_work
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    if _new_work is wake_event:
        _new_work = None
