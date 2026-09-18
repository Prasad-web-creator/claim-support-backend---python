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
    is_placeholder_policy_name,
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

# The real units of work one policy goes through, in order. Progress is counted
# in these steps as they actually finish — never on a timer.
POLICY_STAGES = (
    "policy_extraction",
    "validation",
    "rule_engine",
    "evidence_retrieval",
    "coverage_analysis",
    "report",
)
POLICY_TOTAL_STEPS = len(POLICY_STAGES)

STAGE_LABELS = {
    "queued": "Waiting to start",
    "prescription_extraction": "Reading your prescription",
    "policy_extraction": "Reading the policy document",
    "validation": "Checking policy and prescription details",
    "rule_engine": "Applying policy rules",
    "evidence_retrieval": "Finding the relevant policy clauses",
    "coverage_analysis": "Evaluating coverage",
    "report": "Preparing the report",
    "waiting_for_user": "Waiting for your answer",
    "done": "Finished",
    "failed": "Stopped",
}


def stage_label(stage: str) -> str:
    """Human wording for a stage key; unknown keys degrade to the raw key."""
    return STAGE_LABELS.get(stage or "", stage or "")


async def _update_entry(
    parent: MultiPolicyAnalysisSession,
    policy_id: str,
    lock: asyncio.Lock,
    **fields,
) -> None:
    """
    Apply changes to one policy slot and persist them immediately.

    Every write goes through the shared lock and re-resolves the slot first:
    policies run concurrently against ONE parent document, and Beanie rebuilds
    nested sub-documents on save(), so a reference taken earlier is stale.
    Persisting here is what makes the progress readable by a polling client
    while the analysis is still running.
    """
    async with lock:
        live = parent.get_entry(policy_id)
        if live is None:
            return
        for key, value in fields.items():
            setattr(live, key, value)
        await parent.save()


async def _set_stage(
    parent: MultiPolicyAnalysisSession,
    policy_id: str,
    stage: str,
    lock: asyncio.Lock,
    *,
    status: str = "",
    **extra,
) -> None:
    """Record that a policy has actually reached `stage`, and persist it."""
    fields = dict(extra)
    fields["stage"] = stage
    if status:
        fields["status"] = status
    if stage in POLICY_STAGES:
        # Steps genuinely finished = the stages before the one now starting.
        fields["completed_steps"] = POLICY_STAGES.index(stage)
        fields["total_steps"] = POLICY_TOTAL_STEPS
    await _update_entry(parent, policy_id, lock, **fields)
    _log_stage(str(parent.id), policy_id, stage, status)


def _entry_progress(entry: PolicyAnalysisEntry) -> tuple:
    """(completed, total) steps for one policy, clamped to sane values."""
    total = entry.total_steps or POLICY_TOTAL_STEPS
    if entry.status in TERMINAL_STATUSES:
        # Nothing is left to do for this policy, however it ended.
        return total, total
    return max(0, min(entry.completed_steps or 0, total)), total


def _session_progress(parent: MultiPolicyAnalysisSession) -> dict:
    """
    Aggregate real progress: the shared prescription step plus every policy's
    finished steps, out of the total the session actually plans to run.
    """
    completed = 1 if parent.prescription_stage == "ready" else 0
    total = 1

    running_stages = []
    for entry in parent.policy_analyses:
        done, entry_total = _entry_progress(entry)
        completed += done
        total += entry_total
        if entry.status not in TERMINAL_STATUSES and entry.status != "waiting_for_user":
            running_stages.append(entry.stage or "queued")

    if any(e.status == "waiting_for_user" for e in parent.policy_analyses):
        label = STAGE_LABELS["waiting_for_user"]
    elif parent.prescription_stage != "ready":
        label = STAGE_LABELS["prescription_extraction"]
    elif running_stages:
        # Report the least-advanced step still in flight: that is the work the
        # session is genuinely still waiting on.
        earliest = min(
            running_stages,
            key=lambda st: POLICY_STAGES.index(st) if st in POLICY_STAGES else -1,
        )
        label = stage_label(earliest)
    else:
        label = STAGE_LABELS["done"]

    return {
        "completedSteps": completed,
        "totalSteps": total,
        "percent": int(round(completed * 100 / total)) if total else 0,
        "label": label,
        "policiesFinished": sum(1 for e in parent.policy_analyses if e.status in TERMINAL_STATUSES),
        "policyCount": len(parent.policy_analyses),
    }


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


async def _label_entry_from_extraction(
    parent: MultiPolicyAnalysisSession,
    policy_id: str,
    policy_json,
    lock: asyncio.Lock,
) -> None:
    """
    Name a policy row from its extracted document.

    Only replaces a placeholder name, so a policy the user named keeps that
    name, and only ever fills in a missing insurer.
    """
    if not isinstance(policy_json, dict):
        return

    entry = parent.get_entry(policy_id)
    if entry is None:
        return

    fields = {}

    extracted_name = str(policy_json.get("policyName") or "").strip()
    if extracted_name and is_placeholder_policy_name(entry.policy_name):
        fields["policy_name"] = extracted_name[:255]

    insurer = str(policy_json.get("insuranceCompany") or "").strip()
    if insurer and not str(entry.insurance_company or "").strip():
        fields["insurance_company"] = insurer[:255]

    if fields:
        await _update_entry(parent, policy_id, lock, **fields)


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
    lock: Optional[asyncio.Lock] = None,
    running_status: str = "analyzing",
) -> Optional[PolicyAnalysisEntry]:
    """
    Run the full existing pipeline for exactly one policy, in its own child session.

    Each real step is written to the parent document the moment it is reached, so
    a client polling the session sees progress that reflects work actually done.
    Never raises: every failure is captured on the entry so sibling policies are
    unaffected.
    """
    parent_id = str(parent.id)
    lock = lock or asyncio.Lock()
    start_time = time.time()

    async with semaphore:
        session = None
        try:
            # Step 1 — policy document text + structured extraction.
            await _set_stage(
                parent,
                policy_id,
                "policy_extraction",
                lock,
                status=running_status,
                started_at=datetime.now(timezone.utc),
            )

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
            await _update_entry(parent, policy_id, lock, session_id=str(session.id))

            # Isolate the cost tracker per policy. contextvars are copied per
            # task, so this never leaks into a sibling analysis.
            set_current_cost_tracker(AnalysisCostTracker(session_id=str(session.id)))

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

            # Step 2 — validate/normalize both halves for this policy.
            await _set_stage(parent, policy_id, "validation", lock, status=running_status)

            # A policy uploaded moments ago is still called "Uploaded Policy";
            # now that its document has been read, label the row with the real
            # policy so the comparison names what it is comparing.
            await _label_entry_from_extraction(parent, policy_id, policy_json_raw, lock)

            # Deep copy so one policy's validation/normalization/clarification
            # mutations can never bleed into another policy's analysis.
            validation = validate_extracted_json(
                copy.deepcopy(policy_json_raw) if policy_json_raw else policy_json_raw,
                copy.deepcopy(prescription_json),
            )

            # Steps 3-5 are reported by the single-policy pipeline itself as it
            # reaches them, so the client is never told a step is done early.
            async def _on_stage(stage: str) -> None:
                await _set_stage(parent, policy_id, stage, lock, status=running_status)

            result = await run_single_policy_analysis(
                session,
                user_id=user_id,
                validation=validation,
                policy_ctx=policy_ctx,
                policy_text=policy_text,
                is_manual_rx=rx_ctx.is_manual,
                start_time=start_time,
                background_tasks=background_tasks,
                on_stage=_on_stage,
            )

            async with lock:
                entry = parent.get_entry(policy_id)
                if entry is not None:
                    _entry_from_result(entry, result)
                    if entry.status == "waiting_for_user":
                        # Work is genuinely paused here: leave the finished-step
                        # count where it stopped rather than inflating it.
                        entry.stage = "waiting_for_user"
                    else:
                        entry.stage = "done"
                        entry.completed_steps = entry.total_steps or POLICY_TOTAL_STEPS
                await parent.save()

            entry = parent.get_entry(policy_id)
            _log_stage(parent_id, policy_id, "finished", entry.status if entry else "")
            return entry

        except Exception as e:
            async with lock:
                entry = parent.get_entry(policy_id)
                if entry is not None:
                    # The stage reached before the error is the honest failure point.
                    entry.failed_at_stage = entry.stage or "policy_analysis"
                    entry.status = "failed"
                    entry.stage = "failed"
                    entry.error_message = str(e)
                    entry.completed_at = datetime.now(timezone.utc)
                await parent.save()
            _log_stage(parent_id, policy_id, "failed", "failed", detail=type(e).__name__)
            logger.error(f"[MultiPolicy] session={parent_id} policy={policy_id} error: {e}", exc_info=True)
            if session is not None:
                try:
                    session.status = "failed"
                    await session.save()
                except Exception:
                    pass
            return parent.get_entry(policy_id)


async def create_multi_policy_session(
    user_id: str,
    prescription_id: str,
    policy_ids: List[str],
) -> MultiPolicyAnalysisSession:
    """
    Validate ownership and create the parent session with one queued slot per
    policy — nothing is analysed yet.

    Split out from the run so the API can hand the client a session id (and
    therefore a pollable progress source) before the long work begins.
    """
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
        prescription_stage="queued",
        policy_analyses=[
            PolicyAnalysisEntry(
                policy_id=str(p.id),
                policy_name=p.policy_name or p.policy_number or "",
                insurance_company=p.insurance_company or "",
                status="queued",
                stage="queued",
                completed_steps=0,
                total_steps=POLICY_TOTAL_STEPS,
            )
            for p in resolved_policies
        ],
    )
    await parent.insert()
    return parent


async def run_multi_policy_analysis(
    parent: MultiPolicyAnalysisSession,
    user_id: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    """
    Do the actual work for an already-created parent session.

    Every step is persisted as it completes, so `get_multi_policy_session` (and
    thus a polling client) reports true progress while this is still running.
    """
    settings = get_settings()
    start_time = time.time()
    parent_id = str(parent.id)

    try:
        # ─── Step 1: prescription extracted ONCE, reused by every policy ──────
        parent.prescription_stage = "extracting"
        await parent.save()

        rx_ctx, rx_text, prescription_json = await _extract_shared_prescription(parent, user_id)
        parent.prescription_text = rx_text
        parent.prescription_json = prescription_json
        parent.is_manual_prescription = rx_ctx.is_manual
        parent.prescription_stage = "ready"
        parent.status = "analyzing"
        await parent.save()

        # ─── Step 2: bounded concurrent per-policy analysis ───────────────────
        # One lock guards writes to the shared parent document: policies run
        # concurrently but each progress update must be a whole-document save.
        lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(settings.MULTI_POLICY_MAX_CONCURRENCY)
        results = await asyncio.gather(
            *[
                _analyze_one_policy(
                    parent,
                    policy_id,
                    user_id=user_id,
                    rx_ctx=rx_ctx,
                    rx_text=rx_text,
                    prescription_json=prescription_json,
                    semaphore=semaphore,
                    background_tasks=background_tasks,
                    lock=lock,
                )
                for policy_id in [e.policy_id for e in parent.policy_analyses]
            ],
            return_exceptions=True,
        )

        # `_analyze_one_policy` never raises, but stay defensive: an unexpected
        # exception must not lose the sibling results.
        for entry, res in zip(parent.policy_analyses, results):
            if isinstance(res, BaseException):
                entry.status = "failed"
                entry.stage = "failed"
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


async def start_multi_policy_analysis(
    user_id: str,
    prescription_id: str,
    policy_ids: List[str],
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    """
    Analyse one prescription against multiple policies and wait for the result.

    Returns the parent session payload with one independently traceable result
    per policy.
    """
    parent = await create_multi_policy_session(user_id, prescription_id, policy_ids)
    return await run_multi_policy_analysis(parent, user_id, background_tasks=background_tasks)


# Detached analysis runs, kept referenced so the event loop cannot garbage
# collect a task that is still doing work.
_DETACHED_RUNS: set = set()


def schedule_multi_policy_analysis(
    parent: MultiPolicyAnalysisSession,
    user_id: str,
) -> "asyncio.Task":
    """
    Run an already-created session in the background and return immediately.

    The caller (the API route) has already returned the session id, so the
    client can poll for real progress while this task works. `background_tasks`
    is deliberately not forwarded: FastAPI's background tasks would no longer be
    executed from here, and the pipeline already falls back to running its
    post-processing inline when none is supplied.
    """

    async def _runner():
        try:
            await run_multi_policy_analysis(parent, user_id, background_tasks=None)
        except Exception as e:
            logger.error(f"[MultiPolicy] session={parent.id} background run failed: {e}")

    task = asyncio.create_task(_runner())
    _DETACHED_RUNS.add(task)
    task.add_done_callback(_DETACHED_RUNS.discard)
    return task


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

    child_session_id = entry.session_id
    lock = asyncio.Lock()

    # Answering restarts this policy at the rule engine, so the finished-step
    # count goes back to what is genuinely done again.
    await _set_stage(parent, policy_id, "rule_engine", lock, status="reanalyzing", questions=[])

    async def _on_stage(stage: str) -> None:
        await _set_stage(parent, policy_id, stage, lock, status="reanalyzing")

    try:
        # Reuses the existing single-policy clarification flow verbatim,
        # including its round limits and answer-handling rules.
        result = await resume_analysis_session(
            session_id=child_session_id,
            user_id=user_id,
            answers=answers,
            background_tasks=background_tasks,
            on_stage=_on_stage,
        )
        async with lock:
            entry = parent.get_entry(policy_id)
            if entry is not None:
                _entry_from_result(entry, result)
                if entry.status == "waiting_for_user":
                    entry.stage = "waiting_for_user"
                else:
                    entry.stage = "done"
                    entry.completed_steps = entry.total_steps or POLICY_TOTAL_STEPS
            await parent.save()
    except Exception as e:
        async with lock:
            entry = parent.get_entry(policy_id)
            if entry is not None:
                entry.status = "failed"
                entry.stage = "failed"
                entry.error_message = str(e)
                entry.failed_at_stage = "clarification_resume"
                entry.completed_at = datetime.now(timezone.utc)
            await parent.save()
        logger.error(f"[MultiPolicy] session={parent.id} policy={policy_id} resume error: {e}", exc_info=True)

    entry = parent.get_entry(policy_id)
    _log_stage(str(parent.id), policy_id, "clarification_resume", entry.status if entry else "")
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
        parent.prescription_stage = "ready"

    # Reset the slot so the retry starts from a clean state — including the
    # progress counters, which must not keep a failed run's finished steps.
    entry.status = "queued"
    entry.stage = "queued"
    entry.completed_steps = 0
    entry.total_steps = POLICY_TOTAL_STEPS
    entry.error_message = ""
    entry.failed_at_stage = ""
    entry.questions = []
    entry.report_id = ""
    entry.session_id = ""
    entry.completed_at = None
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
        "prescriptionStage": parent.prescription_stage or "queued",
        # Real progress: steps actually finished out of the steps this session runs.
        "progress": _session_progress(parent),
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
                "stage": e.stage or "queued",
                "stageLabel": stage_label(e.stage or "queued"),
                "completedSteps": _entry_progress(e)[0],
                "totalSteps": _entry_progress(e)[1],
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
