"""
Multi-Policy Analysis Orchestrator.

Analyses ONE prescription against N policies. The prescription is extracted and
normalized once, then each policy runs the existing single-policy pipeline
through `run_single_policy_analysis` against its own child AnalysisSession.

Policies are fully isolated: each gets its own deep copy of the prescription
JSON, its own business-rule evaluation, its own clarification state and its own
AnalysisReport. One policy failing, needing clarification, or being invalid
never affects the others.
"""

import asyncio
import copy
import time
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import BackgroundTasks
from beanie import PydanticObjectId as ObjectId

from app.core.config import get_settings
from app.core.logging import logger, log_section
from app.models.analysis_session import AnalysisSession
from app.models.multi_policy_session import MultiPolicyAnalysisSession, PolicyAnalysisEntry
from app.models.policy import Policy
from app.services.coverage.policy_comparison import build_comparison_summary
from app.services.extraction.json_validation import validate_extracted_json
from app.services.extraction.policy_extraction import extract_policy_details
from app.services.extraction.prescription_extraction import extract_prescription_details
from app.services.llm.ai_client import AnalysisCostTracker, set_current_cost_tracker
from app.services.analysis_interactive_orchestrator import (
    PolicyContext,
    _apply_manual_prescription_defaults,
    _load_prescription_text,
    _persist_prescription_extraction,
    _process_document,
    _resolve_policy_context,
    _resolve_prescription_context,
    resume_analysis_session,
    run_single_policy_analysis,
)

# Child session statuses that mean "this policy is finished and will not change".
TERMINAL_STATUSES = ("completed", "manual_review_required", "failed", "invalid")


def _log_stage(parent_id: str, policy_id: str, stage: str, status: str = "", detail: str = ""):
    """
    Structured, non-sensitive progress log line.

    Deliberately carries only identifiers and stage/status — never diagnosis,
    patient details or extracted document content.
    """
    msg = f"[MultiPolicy] session={parent_id} policy={policy_id} stage={stage}"
    if status:
        msg += f" status={status}"
    if detail:
        msg += f" detail={detail}"
    logger.info(msg)


def _derive_parent_status(entries: List[PolicyAnalysisEntry]) -> str:
    """
    Roll individual policy states up into one parent status, reusing the
    application's existing status vocabulary.

    waiting_for_user wins over everything else so the UI always surfaces a
    pending question. Otherwise: all good -> completed, all bad -> failed,
    mixed -> partial.
    """
    if not entries:
        return "failed"

    statuses = [e.status for e in entries]

    if any(s in ("queued", "extracting", "analyzing", "reanalyzing") for s in statuses):
        return "analyzing"
    if any(s == "waiting_for_user" for s in statuses):
        return "waiting_for_user"

    succeeded = [s for s in statuses if s in ("completed", "manual_review_required")]
    broken = [s for s in statuses if s in ("failed", "invalid")]

    if succeeded and not broken:
        return "completed"
    if broken and not succeeded:
        return "failed"
    return "partial"


def _attach_entry(parent: MultiPolicyAnalysisSession, entry: PolicyAnalysisEntry) -> None:
    """
    Write an entry back into its parent slot, matched by policy id.

    Beanie rebuilds nested sub-documents on save(), which detaches any entry
    reference taken beforehand. Re-attaching by id keeps a policy's result from
    being silently lost across a save boundary.
    """
    for idx, existing in enumerate(parent.policy_analyses):
        if existing.policy_id == entry.policy_id:
            parent.policy_analyses[idx] = entry
            return


async def _refresh_parent_status(parent: MultiPolicyAnalysisSession) -> MultiPolicyAnalysisSession:
    """Recompute the parent status + comparison summary and persist."""
    parent.status = _derive_parent_status(parent.policy_analyses)
    parent.comparison_summary = build_comparison_summary(parent.policy_analyses)
    await parent.save()
    return parent


def _entry_from_result(entry: PolicyAnalysisEntry, result: dict) -> PolicyAnalysisEntry:
    """Map the single-policy pipeline's return payload onto a policy entry."""
    status = str(result.get("status") or "").lower()

    if status == "needs_clarification":
        entry.status = "waiting_for_user"
        entry.questions = result.get("questions") or []
        entry.session_id = result.get("sessionId") or entry.session_id
        return entry

    entry.questions = []
    entry.overall_status = str(result.get("overallStatus") or "")
    entry.decision_type = str(result.get("decisionType") or "")
    try:
        entry.dominance_score = float(result.get("dominanceScore") or 0.0)
    except (TypeError, ValueError):
        entry.dominance_score = 0.0

    report_id = result.get("_id") or result.get("id")
    if report_id:
        entry.report_id = str(report_id)

    if status == "manual_review_required":
        entry.status = "manual_review_required"
    elif entry.overall_status.startswith("Invalid"):
        # Invalid documents are intentionally never persisted as reports.
        entry.status = "invalid"
        entry.error_message = entry.overall_status
        entry.failed_at_stage = "document_validation"
    else:
        entry.status = "completed"

    entry.completed_at = datetime.now(timezone.utc)
    return entry


async def _extract_shared_prescription(parent: MultiPolicyAnalysisSession, user_id: str):
    """
    Resolve + extract the prescription ONCE for the whole multi-policy session.

    Reuses the persisted extraction cache when present, so N policies never
    trigger N OCR / extraction passes.
    """
    rx_ctx = await _resolve_prescription_context(user_id, parent.prescription_id)

    cached_doc = rx_ctx.rx_doc
    if (
        cached_doc
        and cached_doc.extracted_prescription_text
        and cached_doc.extracted_prescription_json
    ):
        _log_stage(str(parent.id), "-", "prescription_extraction", "cache_hit")
        rx_text = cached_doc.extracted_prescription_text
        prescription_json = copy.deepcopy(cached_doc.extracted_prescription_json)
        if rx_ctx.is_manual:
            _apply_manual_prescription_defaults(prescription_json, rx_text)
        return rx_ctx, rx_text, prescription_json

    _log_stage(str(parent.id), "-", "prescription_extraction", "cache_miss")
    rx_text = await _load_prescription_text(rx_ctx, user_id)
    rx_data = await extract_prescription_details(rx_text)

    # Validate the prescription half on its own; the policy half is validated
    # per policy inside each child analysis.
    rx_validation = validate_extracted_json({}, rx_data.get("extractedJson"))
    prescription_json = rx_validation["validatedPrescriptionJson"]

    if rx_ctx.is_manual:
        _apply_manual_prescription_defaults(prescription_json, rx_text)

    new_rx_id = await _persist_prescription_extraction(
        rx_ctx,
        user_id=user_id,
        rx_text=rx_text,
        prescription_json=prescription_json,
        fallback_original_name=parent.prescription_id or "",
    )
    if new_rx_id:
        parent.prescription_id = new_rx_id

    return rx_ctx, rx_text, prescription_json


async def _analyze_one_policy(
    parent: MultiPolicyAnalysisSession,
    policy_id: str,
    *,
    user_id: str,
    rx_ctx,
    rx_text: str,
    prescription_json: dict,
    semaphore: asyncio.Semaphore,
    background_tasks: Optional[BackgroundTasks],
) -> PolicyAnalysisEntry:
    """
    Run the full existing pipeline for exactly one policy, in its own child session.

    Never raises: every failure is captured on the entry so sibling policies are
    unaffected.
    """
    parent_id = str(parent.id)
    # Resolved here rather than passed in: a parent save before this point would
    # have detached any entry reference held by the caller.
    entry = parent.get_entry(policy_id)
    start_time = time.time()

    async with semaphore:
        session = None
        try:
            entry.started_at = datetime.now(timezone.utc)

            # Each policy gets its own child session — own status, own
            # clarification state, own report.
            session = AnalysisSession(
                user_id=user_id,
                policy_id=policy_id,
                prescription_id=parent.prescription_id,
                parent_session_id=parent_id,
                status="extracting",
            )
            await session.insert()
            entry.session_id = str(session.id)

            # Isolate the cost tracker per policy. contextvars are copied per
            # task, so this never leaks into a sibling analysis.
            set_current_cost_tracker(AnalysisCostTracker(session_id=str(session.id)))

            _log_stage(parent_id, policy_id, "policy_extraction", "started")
            policy_ctx = await _resolve_policy_context(user_id, policy_doc_id=policy_id)

            if policy_ctx.has_cached_text:
                _log_stage(parent_id, policy_id, "policy_extraction", "cache_hit")
                policy_text = policy_ctx.cached_text
                policy_json_raw = policy_ctx.cached_json
            else:
                _log_stage(parent_id, policy_id, "policy_extraction", "cache_miss")
                policy_text = await _process_document(
                    policy_ctx.policy_doc.grid_fs_file_id, user_id, doc_label="Policy Document"
                )
                policy_data = await extract_policy_details(policy_text)
                policy_json_raw = policy_data.get("extractedJson")

            policy_ctx.text = policy_text
            session.policy_text = policy_text
            session.prescription_text = rx_text

            # Deep copy so one policy's validation/normalization/clarification
            # mutations can never bleed into another policy's analysis.
            validation = validate_extracted_json(
                copy.deepcopy(policy_json_raw) if policy_json_raw else policy_json_raw,
                copy.deepcopy(prescription_json),
            )

            _log_stage(parent_id, policy_id, "coverage_analysis", "started")
            result = await run_single_policy_analysis(
                session,
                user_id=user_id,
                validation=validation,
                policy_ctx=policy_ctx,
                policy_text=policy_text,
                is_manual_rx=rx_ctx.is_manual,
                start_time=start_time,
                background_tasks=background_tasks,
            )

            _entry_from_result(entry, result)
            _attach_entry(parent, entry)
            _log_stage(parent_id, policy_id, "finished", entry.status)
            return entry

        except Exception as e:
            entry.status = "failed"
            entry.error_message = str(e)
            entry.failed_at_stage = "policy_analysis"
            entry.completed_at = datetime.now(timezone.utc)
            _attach_entry(parent, entry)
            _log_stage(parent_id, policy_id, "failed", "failed", detail=type(e).__name__)
            logger.error(f"[MultiPolicy] session={parent_id} policy={policy_id} error: {e}", exc_info=True)
            if session is not None:
                try:
                    session.status = "failed"
                    await session.save()
                except Exception:
                    pass
            return entry


async def start_multi_policy_analysis(
    user_id: str,
    prescription_id: str,
    policy_ids: List[str],
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    """
    Analyse one prescription against multiple policies.

    Returns the parent session payload with one independently traceable result
    per policy.
    """
    settings = get_settings()
    start_time = time.time()

    log_section("Multi-Policy Claim Analysis Started")
    logger.info(
        f"[MultiPolicy] Started for user={user_id} prescription={prescription_id} "
        f"policies={len(policy_ids)}"
    )

    # ─── Authorization: every policy must exist and belong to this user ───────
    resolved_policies = []
    for pid in policy_ids:
        try:
            policy = await Policy.get(ObjectId(pid))
        except Exception:
            policy = None
        if not policy or policy.user_id != user_id:
            raise PermissionError(f"Policy '{pid}' not found or not accessible")
        resolved_policies.append(policy)

    parent = MultiPolicyAnalysisSession(
        user_id=user_id,
        prescription_id=prescription_id,
        status="extracting",
        policy_analyses=[
            PolicyAnalysisEntry(
                policy_id=str(p.id),
                policy_name=p.policy_name or p.policy_number or "",
                insurance_company=p.insurance_company or "",
                status="queued",
            )
            for p in resolved_policies
        ],
    )
    await parent.insert()
    parent_id = str(parent.id)

    try:
        # ─── Stage 1: prescription extracted ONCE, reused by every policy ─────
        rx_ctx, rx_text, prescription_json = await _extract_shared_prescription(parent, user_id)
        parent.prescription_text = rx_text
        parent.prescription_json = prescription_json
        parent.is_manual_prescription = rx_ctx.is_manual
        parent.status = "analyzing"
        await parent.save()

        # ─── Stage 2: bounded concurrent per-policy analysis ──────────────────
        semaphore = asyncio.Semaphore(settings.MULTI_POLICY_MAX_CONCURRENCY)
        results = await asyncio.gather(
            *[
                _analyze_one_policy(
                    parent,
                    entry.policy_id,
                    user_id=user_id,
                    rx_ctx=rx_ctx,
                    rx_text=rx_text,
                    prescription_json=prescription_json,
                    semaphore=semaphore,
                    background_tasks=background_tasks,
                )
                for entry in list(parent.policy_analyses)
            ],
            return_exceptions=True,
        )

        # `_analyze_one_policy` never raises, but stay defensive: an unexpected
        # exception must not lose the sibling results.
        for entry, res in zip(parent.policy_analyses, results):
            if isinstance(res, BaseException):
                entry.status = "failed"
                entry.error_message = str(res)
                entry.failed_at_stage = "orchestration"
                _attach_entry(parent, entry)
                logger.error(f"[MultiPolicy] session={parent_id} unexpected error: {res}")

        parent.total_processing_time_ms = int((time.time() - start_time) * 1000)
        await _refresh_parent_status(parent)

        logger.info(
            f"[MultiPolicy] session={parent_id} finished status={parent.status} "
            f"({parent.total_processing_time_ms}ms)"
        )
        return serialize_multi_session(parent)

    except Exception as e:
        logger.error(f"[MultiPolicy] session={parent_id} fatal error: {e}", exc_info=True)
        parent.status = "failed"
        parent.error_message = str(e)
        parent.total_processing_time_ms = int((time.time() - start_time) * 1000)
        await parent.save()
        raise


async def resume_policy_analysis(
    parent_session_id: str,
    policy_id: str,
    user_id: str,
    answers: dict,
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    """
    Answer clarification questions for ONE policy inside a multi-policy session.

    Only the affected policy is resumed; every sibling policy keeps its result.
    """
    parent = await _get_owned_parent(parent_session_id, user_id)

    entry = parent.get_entry(policy_id)
    if not entry:
        raise ValueError(f"Policy '{policy_id}' is not part of this analysis session")
    if entry.status != "waiting_for_user" or not entry.session_id:
        raise ValueError(
            f"Policy '{policy_id}' is not waiting for clarification (current status: {entry.status})"
        )

    _log_stage(str(parent.id), policy_id, "clarification_resume", "started")

    entry.status = "reanalyzing"
    await parent.save()
    entry = parent.get_entry(policy_id)   # save() detaches the previous reference

    try:
        # Reuses the existing single-policy clarification flow verbatim,
        # including its round limits and answer-handling rules.
        result = await resume_analysis_session(
            session_id=entry.session_id,
            user_id=user_id,
            answers=answers,
            background_tasks=background_tasks,
        )
        _entry_from_result(entry, result)
    except Exception as e:
        entry.status = "failed"
        entry.error_message = str(e)
        entry.failed_at_stage = "clarification_resume"
        entry.completed_at = datetime.now(timezone.utc)
        logger.error(f"[MultiPolicy] session={parent.id} policy={policy_id} resume error: {e}", exc_info=True)

    _attach_entry(parent, entry)
    _log_stage(str(parent.id), policy_id, "clarification_resume", entry.status)
    await _refresh_parent_status(parent)
    return serialize_multi_session(parent)


async def retry_policy_analysis(
    parent_session_id: str,
    policy_id: str,
    user_id: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    """
    Retry a single failed policy without touching its siblings.

    Idempotent by design: a policy that already produced a result is returned
    as-is rather than analysed again, so retries never create duplicate reports.
    """
    parent = await _get_owned_parent(parent_session_id, user_id)

    entry = parent.get_entry(policy_id)
    if not entry:
        raise ValueError(f"Policy '{policy_id}' is not part of this analysis session")

    if entry.status not in ("failed", "invalid"):
        logger.info(
            f"[MultiPolicy] session={parent.id} policy={policy_id} retry skipped "
            f"(status={entry.status}) — returning existing result"
        )
        return serialize_multi_session(parent)

    rx_ctx = await _resolve_prescription_context(user_id, parent.prescription_id)
    rx_text = parent.prescription_text or ""
    prescription_json = copy.deepcopy(parent.prescription_json or {})

    if not rx_text or not prescription_json:
        rx_ctx, rx_text, prescription_json = await _extract_shared_prescription(parent, user_id)
        parent.prescription_text = rx_text
        parent.prescription_json = prescription_json

    # Reset the slot so the retry starts from a clean state.
    entry.status = "queued"
    entry.error_message = ""
    entry.failed_at_stage = ""
    entry.questions = []
    entry.report_id = ""
    entry.session_id = ""
    await parent.save()

    await _analyze_one_policy(
        parent,
        policy_id,
        user_id=user_id,
        rx_ctx=rx_ctx,
        rx_text=rx_text,
        prescription_json=prescription_json,
        semaphore=asyncio.Semaphore(1),
        background_tasks=background_tasks,
    )

    await _refresh_parent_status(parent)
    return serialize_multi_session(parent)


async def get_multi_policy_session(parent_session_id: str, user_id: str) -> dict:
    """Read the current state of a multi-policy session (safe to poll / refresh)."""
    parent = await _get_owned_parent(parent_session_id, user_id)
    return serialize_multi_session(parent)


async def list_multi_policy_sessions(user_id: str, limit: int = 20) -> List[dict]:
    """Recent multi-policy sessions for a user, newest first."""
    sessions = (
        await MultiPolicyAnalysisSession.find(MultiPolicyAnalysisSession.user_id == user_id)
        .sort("-_id")
        .limit(limit)
        .to_list()
    )
    return [serialize_multi_session(s, include_prescription_json=False) for s in sessions]


async def _get_owned_parent(parent_session_id: str, user_id: str) -> MultiPolicyAnalysisSession:
    """Load a parent session, enforcing ownership."""
    try:
        parent = await MultiPolicyAnalysisSession.get(ObjectId(parent_session_id))
    except Exception:
        parent = None
    if not parent or parent.user_id != user_id:
        raise PermissionError("Analysis session not found")
    return parent


def serialize_multi_session(
    parent: MultiPolicyAnalysisSession,
    include_prescription_json: bool = True,
) -> dict:
    """
    Build the API payload for a multi-policy session.

    Exposes only what the client needs to render per-policy progress, results
    and clarification prompts — no policy text, no internal extraction dumps.
    """
    rx_json = parent.prescription_json or {}
    payload = {
        "sessionId": str(parent.id),
        "prescriptionId": parent.prescription_id,
        "status": parent.status,
        "isManualPrescription": parent.is_manual_prescription,
        "totalProcessingTimeMs": parent.total_processing_time_ms,
        "errorMessage": parent.error_message or "",
        "createdAt": parent.created_at.isoformat() if parent.created_at else None,
        "updatedAt": parent.updated_at.isoformat() if parent.updated_at else None,
        "policyCount": len(parent.policy_analyses),
        "comparisonSummary": parent.comparison_summary or {},
        "policyAnalyses": [
            {
                "policyId": e.policy_id,
                "policyName": e.policy_name or "",
                "insuranceCompany": e.insurance_company or "",
                "status": e.status,
                "overallStatus": e.overall_status or "",
                "decisionType": e.decision_type or "",
                "dominanceScore": e.dominance_score or 0.0,
                "reportId": e.report_id or "",
                "sessionId": e.session_id or "",
                "questions": e.questions or [],
                "errorMessage": e.error_message or "",
                "failedAtStage": e.failed_at_stage or "",
                "startedAt": e.started_at.isoformat() if e.started_at else None,
                "completedAt": e.completed_at.isoformat() if e.completed_at else None,
            }
            for e in parent.policy_analyses
        ],
    }

    if include_prescription_json:
        payload["prescription"] = {
            "patientName": rx_json.get("patientName") or "",
            "diagnosis": rx_json.get("diagnosis") or "",
            "visitDate": rx_json.get("visitDate") or rx_json.get("consultationDate") or "",
        }
    return payload
