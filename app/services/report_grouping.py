"""
Coverage Analysis Report grouping.

The reports list groups every policy analysed against the SAME prescription in
the SAME multi-policy session into one container, instead of showing one loose
entry per policy.

Grouping key is always a stored relationship id — the multi-policy session id —
never a policy name or any display text:

    MultiPolicyAnalysisSession (groupId)
        ├── policy A → AnalysisReport
        ├── policy B → AnalysisReport
        └── policy C → failed / waiting_for_user (no report yet)

Reports produced by the classic single-policy flow have no parent session, so
each one is returned as its own single-policy group. That covers every
historical report, which predates the parentSessionId field entirely.
"""

import math
from typing import Any, Optional

from beanie import PydanticObjectId as ObjectId

from app.core.logging import logger
from app.models.analysis_report import AnalysisReport
from app.models.multi_policy_session import MultiPolicyAnalysisSession
from app.services.coverage.policy_comparison import classify_outcome

# Matches reports that are NOT part of a multi-policy session, including
# historical documents written before parentSessionId existed.
_UNGROUPED_REPORT_CLAUSE = {
    "$or": [
        {"parentSessionId": {"$exists": False}},
        {"parentSessionId": None},
        {"parentSessionId": ""},
    ]
}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _created_at(doc) -> Any:
    """Creation time for merge-sorting, falling back to the ObjectId timestamp."""
    created = getattr(doc, "created_at", None)
    if created:
        return created
    try:
        return doc.id.generation_time
    except Exception:
        return None


def _clean(value) -> str:
    """Drop placeholder values the extraction pipeline may leave behind."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in ("unknown", "none", "n/a", "null", "not specified", "undefined"):
        return ""
    return text


def _prescription_summary(prescription_json: Optional[dict]) -> dict:
    rx = prescription_json or {}
    return {
        "diagnosis": _clean(rx.get("diagnosis")),
        "patientName": _clean(rx.get("patientName") or rx.get("patient")),
        "visitDate": _clean(rx.get("visitDate") or rx.get("consultationDate")),
    }


def _report_number_label(report_number) -> str:
    if report_number is None:
        return ""
    return f"CR-{str(report_number).zfill(4)}"


def _policy_row_from_report(report: AnalysisReport) -> dict:
    """One policy row built from a persisted report."""
    policy_json = report.policy_json or {}
    policy_name = _clean(
        policy_json.get("policyName")
        or policy_json.get("planName")
        or policy_json.get("insuranceCompany")
    )
    return {
        "policyId": report.policy_id or "",
        "policyName": policy_name,
        "policyNumber": _clean(policy_json.get("policyNumber")),
        "insuranceCompany": _clean(policy_json.get("insuranceCompany")),
        "policyStartDate": _clean(policy_json.get("policyStartDate") or policy_json.get("startDate") or policy_json.get("effectiveFrom")),
        "policyEndDate": _clean(policy_json.get("policyEndDate") or policy_json.get("endDate") or policy_json.get("effectiveTo")),
        "status": report.status or "completed",
        "overallStatus": report.overall_status or "",
        "outcome": classify_outcome(report.status or "completed", report.overall_status or ""),
        "decisionType": report.decision_type or "",
        "dominanceScore": report.dominance_score or 0.0,
        "confidenceScore": report.confidence_score or 0,
        "processingTimeMs": report.processing_time_ms or 0,
        "reportId": str(report.id),
        "reportNumber": report.report_number,
        "reportLabel": _report_number_label(report.report_number),
        "sessionId": report.session_id or "",
        "errorMessage": report.error_message or "",
        "questions": [],
    }


def _policy_row_from_entry(entry, report: Optional[AnalysisReport]) -> dict:
    """
    One policy row inside a multi-policy group.

    The session entry is the source of truth for status (it also covers policies
    that failed, are waiting for clarification, or produced no report). When a
    report exists, its numbering and timings are layered on top.
    """
    policy_json = (report.policy_json or {}) if report else {}
    policy_name = (
        _clean(entry.policy_name)
        or _clean(policy_json.get("policyName"))
        or _clean(entry.insurance_company)
        or _clean(policy_json.get("insuranceCompany"))
    )
    return {
        "policyId": entry.policy_id,
        "policyName": policy_name,
        "policyNumber": _clean(policy_json.get("policyNumber")),
        "insuranceCompany": _clean(entry.insurance_company) or _clean(policy_json.get("insuranceCompany")),
        "policyStartDate": _clean(policy_json.get("policyStartDate") or policy_json.get("startDate") or policy_json.get("effectiveFrom")),
        "policyEndDate": _clean(policy_json.get("policyEndDate") or policy_json.get("endDate") or policy_json.get("effectiveTo")),
        "status": entry.status,
        "overallStatus": entry.overall_status or "",
        "outcome": classify_outcome(entry.status, entry.overall_status or ""),
        "decisionType": entry.decision_type or "",
        "dominanceScore": entry.dominance_score or 0.0,
        "confidenceScore": (report.confidence_score or 0) if report else 0,
        "processingTimeMs": (report.processing_time_ms or 0) if report else 0,
        "reportId": entry.report_id or "",
        "reportNumber": report.report_number if report else None,
        "reportLabel": _report_number_label(report.report_number if report else None),
        "sessionId": entry.session_id or "",
        "errorMessage": entry.error_message or "",
        "failedAtStage": entry.failed_at_stage or "",
        "questions": entry.questions or [],
        "startedAt": _iso(entry.started_at),
        "completedAt": _iso(entry.completed_at),
    }


def _single_group(report: AnalysisReport) -> dict:
    """A classic single-policy analysis, rendered as a group of one."""
    row = _policy_row_from_report(report)
    created = _created_at(report)
    return {
        "groupId": str(report.id),
        "groupType": "single",
        "sessionId": report.session_id or "",
        "prescriptionId": report.prescription_id or "",
        "prescription": _prescription_summary(report.prescription_json),
        "status": report.status or "completed",
        "policyCount": 1,
        "completedCount": 1 if report.status in ("completed", "manual_review_required") else 0,
        "totalProcessingTimeMs": report.processing_time_ms or 0,
        "reportIds": [str(report.id)],
        "createdAt": _iso(created),
        "updatedAt": _iso(report.updated_at) or _iso(created),
        "policyReports": [row],
    }


def _multi_group(session: MultiPolicyAnalysisSession, reports_by_id: dict) -> dict:
    """A multi-policy analysis: one prescription, every policy under one group."""
    rows = []
    report_ids = []
    for entry in session.policy_analyses:
        report = reports_by_id.get(entry.report_id) if entry.report_id else None
        if report is not None:
            report_ids.append(str(report.id))
        rows.append(_policy_row_from_entry(entry, report))

    completed = sum(
        1 for e in session.policy_analyses
        if e.status in ("completed", "manual_review_required")
    )
    created = _created_at(session)

    return {
        "groupId": str(session.id),
        "groupType": "multi",
        "sessionId": str(session.id),
        "prescriptionId": session.prescription_id or "",
        "prescription": _prescription_summary(session.prescription_json),
        "status": session.status,
        "policyCount": len(session.policy_analyses),
        "completedCount": completed,
        "totalProcessingTimeMs": session.total_processing_time_ms or 0,
        "reportIds": report_ids,
        "comparisonSummary": session.comparison_summary or {},
        "createdAt": _iso(created),
        "updatedAt": _iso(session.updated_at) or _iso(created),
        "policyReports": rows,
    }


async def list_report_groups(user_id: str, page: int = 1, limit: int = 10) -> dict:
    """
    Paginated, newest-first list of analysis groups for a user.

    Multi-policy sessions and ungrouped single reports live in different
    collections, so a page is produced by reading the first `skip + limit` of
    each, merge-sorting by creation time and slicing the requested window.
    """
    page = max(1, page)
    limit = max(1, limit)
    skip = (page - 1) * limit
    window = skip + limit

    session_query = MultiPolicyAnalysisSession.find(
        MultiPolicyAnalysisSession.user_id == user_id
    )
    report_query = AnalysisReport.find(
        AnalysisReport.user_id == user_id, _UNGROUPED_REPORT_CLAUSE
    )

    total_sessions = await session_query.count()
    total_reports = await report_query.count()
    total = total_sessions + total_reports

    sessions = await session_query.sort("-_id").limit(window).to_list()
    single_reports = await report_query.sort("-_id").limit(window).to_list()

    # Pull the reports referenced by the multi-policy sessions in this window,
    # so each policy row can show its report number and timings.
    referenced_ids = []
    for session in sessions:
        for entry in session.policy_analyses:
            if entry.report_id:
                try:
                    referenced_ids.append(ObjectId(entry.report_id))
                except Exception:
                    continue

    reports_by_id: dict = {}
    if referenced_ids:
        linked = await AnalysisReport.find(
            {"_id": {"$in": referenced_ids}, "userId": user_id}
        ).to_list()
        reports_by_id = {str(r.id): r for r in linked}

    groups = [_multi_group(s, reports_by_id) for s in sessions]
    groups += [_single_group(r) for r in single_reports]

    groups.sort(key=lambda g: g.get("createdAt") or "", reverse=True)
    page_groups = groups[skip:window]

    total_pages = math.ceil(total / limit) if limit > 0 else 1
    logger.debug(
        f"[ReportGroups] user={user_id} page={page} "
        f"sessions={total_sessions} singles={total_reports} returned={len(page_groups)}"
    )

    return {
        "docs": page_groups,
        "totalDocs": total,
        "limit": limit,
        "page": page,
        "totalPages": total_pages,
        "pagingCounter": skip + 1,
        "hasPrevPage": page > 1,
        "hasNextPage": page < total_pages,
        "prevPage": page - 1 if page > 1 else None,
        "nextPage": page + 1 if page < total_pages else None,
    }


async def delete_report_group(user_id: str, group_id: str) -> dict:
    """
    Delete a whole group.

    For a multi-policy group that means every report it produced plus the
    session itself; for a single-policy group it is just that one report.
    """
    session = None
    try:
        session = await MultiPolicyAnalysisSession.get(ObjectId(group_id))
    except Exception:
        session = None

    if session and session.user_id == user_id:
        deleted = 0
        for entry in session.policy_analyses:
            if not entry.report_id:
                continue
            try:
                report = await AnalysisReport.get(ObjectId(entry.report_id))
            except Exception:
                report = None
            if report and report.user_id == user_id:
                await report.delete()
                deleted += 1

        # A report whose id never made it back into policy_analyses (analysis
        # still running at delete time, a retry that rewrote the entry, a write
        # that failed) would survive the loop above. It would then be invisible:
        # excluded from the list by _UNGROUPED_REPORT_CLAUSE because it claims a
        # parent, while the parent no longer exists — yet still counted on the
        # dashboard. Sweep by parentSessionId so the group cannot leave orphans.
        sweep = await AnalysisReport.find(
            AnalysisReport.user_id == user_id,
            {"parentSessionId": group_id},
        ).to_list()
        for report in sweep:
            await report.delete()
            deleted += 1

        await session.delete()
        logger.info(
            f"[ReportGroups] Deleted multi-policy group {group_id} "
            f"({deleted} report(s)) for user={user_id}"
        )
        return {"deletedGroups": 1, "deletedReports": deleted}

    # Fall back to a single report id
    try:
        report = await AnalysisReport.get(ObjectId(group_id))
    except Exception:
        report = None

    if not report or report.user_id != user_id:
        return {"deletedGroups": 0, "deletedReports": 0}

    await report.delete()
    return {"deletedGroups": 1, "deletedReports": 1}
