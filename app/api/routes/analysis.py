"""
Analysis routes — migrated from analysisController.js.
"""

import math
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, Header
from typing import Optional, List, Any
from pydantic import BaseModel, Field, StrictBool, model_validator

from app.middleware.auth import get_current_user
from app.middleware.rate_limiter import limiter
from app.services.analysis_interactive_orchestrator import start_analysis_session, resume_analysis_session
from app.services.analysis_orchestrator import public_analysis_error
from app.services.multi_policy_orchestrator import (
    create_multi_policy_session,
    schedule_multi_policy_analysis,
    serialize_multi_session,
    resume_policy_analysis,
    retry_policy_analysis,
    get_multi_policy_session,
)
from app.services.report_grouping import delete_report_group, get_report_group, list_report_groups
from app.services.analysis_jobs import (
    cancel_job, create_job, enqueue_answer, enqueue_job, enqueue_prescription, enqueue_retry, get_job, list_jobs,
    require_analysis_consent,
)
from app.core.config import get_settings
from app.models.analysis_report import AnalysisReport
from app.services.crud_service import CrudService

router = APIRouter(prefix="/analysis", tags=["Analysis"])


async def _legacy_consent(
    user: dict = Depends(get_current_user),
    job_id: str | None = Header(None, alias="X-Analysis-Job-Id"),
) -> None:
    await require_analysis_consent(user["id"], job_id)


class AnalysisRequest(BaseModel):
    policyPath: Optional[str] = None
    policyId: Optional[str] = None
    prescriptionPath: str

    @model_validator(mode='before')
    @classmethod
    def check_exactly_one_policy_source(cls, values):
        if isinstance(values, dict):
            has_path = bool(values.get('policyPath'))
            has_id = bool(values.get('policyId'))
            if has_path == has_id:
                raise ValueError('Exactly one of policyPath or policyId must be provided.')
        return values


@router.post("/start", dependencies=[Depends(_legacy_consent)])
@limiter.limit("5/minute")
async def start_analysis(
    request: Request,
    body: AnalysisRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Start the interactive analysis pipeline asynchronously.
    """
    try:
        result = await start_analysis_session(
            user_id=current_user["id"],
            policy_file_id=body.policyPath,
            policy_doc_id=body.policyId,
            prescription_id=body.prescriptionPath,
            background_tasks=background_tasks
        )
        # It could be needs_clarification or complete
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=public_analysis_error(e)) from e

class AnswerRequest(BaseModel):
    answers: dict


class MultiAnalysisRequest(BaseModel):
    """One prescription analysed against one or more policies."""
    prescriptionPath: str
    policyIds: List[str]

    @model_validator(mode='after')
    def validate_policy_ids(self):
        settings = get_settings()
        cleaned = [str(p).strip() for p in (self.policyIds or []) if str(p).strip()]
        if not cleaned:
            raise ValueError('At least one policy must be supplied.')

        # Normalize duplicates — selecting the same policy twice is the same analysis.
        seen = set()
        deduped = []
        for pid in cleaned:
            if pid not in seen:
                seen.add(pid)
                deduped.append(pid)

        if len(deduped) > settings.MAX_POLICIES_PER_ANALYSIS:
            raise ValueError(
                f'A maximum of {settings.MAX_POLICIES_PER_ANALYSIS} policies can be analysed at once.'
            )

        self.policyIds = deduped
        return self


class ConsentRequest(BaseModel):
    agreementVersion: str = Field(min_length=1, max_length=40)
    accepted: StrictBool
    requestId: str | None = Field(default=None, min_length=8, max_length=128)


class JobAnswerRequest(AnswerRequest):
    policyId: str | None = None


class JobPrescriptionRequest(BaseModel):
    prescriptionPath: str


class JobRetryRequest(BaseModel):
    policyId: str


@router.post("/jobs", status_code=201)
@limiter.limit("10/minute")
async def create_analysis_job(request: Request, body: ConsentRequest, user: dict = Depends(get_current_user)):
    return await create_job(user["id"], body.agreementVersion, body.accepted, body.requestId)


@router.get("/jobs")
async def list_analysis_jobs(user: dict = Depends(get_current_user)):
    return {"jobs": await list_jobs(user["id"])}


@router.get("/jobs/{job_id}")
async def read_analysis_job(job_id: str, user: dict = Depends(get_current_user)):
    return await get_job(user["id"], job_id)


@router.post("/jobs/{job_id}/start", status_code=202)
@limiter.limit("10/minute")
async def start_analysis_job(
    request: Request, job_id: str, body: MultiAnalysisRequest, user: dict = Depends(get_current_user),
):
    return await enqueue_job(user["id"], job_id, body.prescriptionPath, body.policyIds)


@router.post("/jobs/{job_id}/prescription", status_code=202)
@limiter.limit("10/minute")
async def prepare_analysis_prescription(
    request: Request, job_id: str, body: JobPrescriptionRequest, user: dict = Depends(get_current_user),
):
    return await enqueue_prescription(user["id"], job_id, body.prescriptionPath)


@router.post("/jobs/{job_id}/answer", status_code=202)
@limiter.limit("30/minute")
async def answer_analysis_job(
    request: Request, job_id: str, body: JobAnswerRequest, user: dict = Depends(get_current_user),
):
    return await enqueue_answer(user["id"], job_id, body.policyId, body.answers)


@router.post("/jobs/{job_id}/retry", status_code=202)
@limiter.limit("10/minute")
async def retry_analysis_job(
    request: Request, job_id: str, body: JobRetryRequest, user: dict = Depends(get_current_user),
):
    return await enqueue_retry(user["id"], job_id, body.policyId)


@router.post("/jobs/{job_id}/cancel")
async def cancel_analysis_job(job_id: str, user: dict = Depends(get_current_user)):
    return await cancel_job(user["id"], job_id)


@router.post("/start-multi", dependencies=[Depends(_legacy_consent)])
@limiter.limit("5/minute")
async def start_multi_analysis(
    request: Request,
    body: MultiAnalysisRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Start a multi-policy analysis: one prescription against N policies.
    Each policy is analysed independently and keeps its own result.

    Returns as soon as the session exists, so the client has a session id to
    poll `GET /analysis/multi/{session_id}` with while the work runs. That poll
    reports progress recorded from steps the pipeline has actually completed.
    """
    try:
        session = await create_multi_policy_session(
            user_id=current_user["id"],
            prescription_id=body.prescriptionPath,
            policy_ids=body.policyIds,
        )
        schedule_multi_policy_analysis(session, current_user["id"])
        return {"success": True, **serialize_multi_session(session)}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=public_analysis_error(e)) from e


@router.get("/multi/{session_id}")
async def get_multi_analysis(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Current state of a multi-policy session, including per-policy results."""
    try:
        return {
            "success": True,
            **await get_multi_policy_session(session_id, current_user["id"])
        }
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=public_analysis_error(e)) from e


@router.post("/multi/{session_id}/{policy_id}/answer", dependencies=[Depends(_legacy_consent)])
@limiter.limit("30/minute")
async def answer_multi_clarification(
    request: Request,
    session_id: str,
    policy_id: str,
    body: AnswerRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Answer clarification questions for ONE policy; only that policy resumes."""
    try:
        result = await resume_policy_analysis(
            parent_session_id=session_id,
            policy_id=policy_id,
            user_id=current_user["id"],
            answers=body.answers,
            background_tasks=background_tasks,
            # Re-analysis runs in the background; the app follows it by polling,
            # so a long AI step can never time out the request.
            wait=False,
        )
        return {"success": True, **result}
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=public_analysis_error(e)) from e


class BatchDeleteGroupsRequest(BaseModel):
    ids: list[str]


@router.get("/grouped")
async def list_grouped_analyses(
    page: int = 1,
    limit: int = 10,
    current_user: dict = Depends(get_current_user)
):
    """
    Coverage Analysis Reports, grouped by analysis.

    Policies analysed together against one prescription are returned as a single
    group; classic single-policy reports are returned as groups of one.
    """
    return await list_report_groups(current_user["id"], page=page, limit=limit)


@router.get("/grouped/{group_id}")
async def get_grouped_analysis(group_id: str, current_user: dict = Depends(get_current_user)):
    group = await get_report_group(current_user["id"], group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return group


@router.post("/grouped/batch-delete")
async def batch_delete_grouped_analyses(
    body: BatchDeleteGroupsRequest,
    current_user: dict = Depends(get_current_user)
):
    """Delete whole analysis groups (all reports belonging to each group)."""
    deleted_groups = 0
    deleted_reports = 0
    for group_id in body.ids:
        result = await delete_report_group(current_user["id"], group_id)
        deleted_groups += result["deletedGroups"]
        deleted_reports += result["deletedReports"]

    return {
        "success": True,
        "deletedCount": deleted_groups,
        "deletedReports": deleted_reports,
        "skippedCount": len(body.ids) - deleted_groups,
        "message": f"Successfully deleted {deleted_groups} analysis group(s)"
    }


@router.delete("/grouped/{group_id}")
async def delete_grouped_analysis(
    group_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete one analysis group and every report inside it."""
    result = await delete_report_group(current_user["id"], group_id)
    if result["deletedGroups"] == 0:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"success": True, **result}


@router.post("/multi/{session_id}/{policy_id}/retry", dependencies=[Depends(_legacy_consent)])
@limiter.limit("30/minute")
async def retry_multi_policy(
    request: Request,
    session_id: str,
    policy_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Retry a single failed policy without re-running the others."""
    try:
        result = await retry_policy_analysis(
            parent_session_id=session_id,
            policy_id=policy_id,
            user_id=current_user["id"],
            background_tasks=background_tasks,
            wait=False,
        )
        return {"success": True, **result}
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=public_analysis_error(e)) from e



@router.post("/{session_id}/answer", dependencies=[Depends(_legacy_consent)])
@limiter.limit("5/minute")
async def answer_clarification(
    request: Request,
    session_id: str,
    body: AnswerRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Resume the analysis pipeline with answers.
    """
    try:
        result = await resume_analysis_session(
            session_id=session_id,
            user_id=current_user["id"],
            answers=body.answers,
            background_tasks=background_tasks
        )
        return {
            "success": True,
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=public_analysis_error(e)) from e


@router.get("")
async def list_analyses(
    page: int = 1,
    limit: int = 10,
    search: str = "",
    current_user: dict = Depends(get_current_user)
):
    """Get all analysis reports for user (paginated)."""
    from app.models.analysis_report import AnalysisReport
    user_id = current_user["id"]
    
    # Simple pagination
    skip = (page - 1) * limit
    
    # We only return reports for this user
    query = AnalysisReport.find(AnalysisReport.user_id == user_id)
    
    total = await query.count()
    reports = await query.sort("-_id").skip(skip).limit(limit).to_list()
    
    # Mobile app expects { docs, totalDocs, limit, page, totalPages } from mongoose-paginate
    import math
    total_pages = math.ceil(total / limit) if limit > 0 else 1
    
    reports_dict = []
    for r in reports:
        d = r.dict(by_alias=True)
        if "_id" in d and d["_id"]:
            d["_id"] = str(d["_id"])
            d["id"] = d["_id"]
        elif "id" in d and d["id"]:
            d["_id"] = str(d["id"])
            d["id"] = d["_id"]
            
        if not d.get("createdAt") and r.id:
            try:
                d["createdAt"] = r.id.generation_time.isoformat()
            except Exception:
                pass
        if not d.get("updatedAt") and d.get("createdAt"):
            d["updatedAt"] = d["createdAt"]

        reports_dict.append(d)
    
    return {
        "docs": reports_dict,
        "totalDocs": total,
        "limit": limit,
        "page": page,
        "totalPages": total_pages,
        "pagingCounter": skip + 1,
        "hasPrevPage": page > 1,
        "hasNextPage": page < total_pages,
        "prevPage": page - 1 if page > 1 else None,
        "nextPage": page + 1 if page < total_pages else None
    }


@router.get("/{report_id}")
async def get_analysis(
    report_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get full analysis report by ID."""
    from app.models.analysis_report import AnalysisReport
    from beanie import PydanticObjectId as ObjectId
    try:
        report = await AnalysisReport.get(ObjectId(report_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Analysis report not found")
        
    if not report or report.user_id != current_user["id"]:
        raise HTTPException(status_code=404, detail="Analysis report not found")
        
    d = report.dict(by_alias=True)
    if "_id" in d and d["_id"]:
        d["_id"] = str(d["_id"])
        d["id"] = d["_id"]
    elif "id" in d and d["id"]:
        d["_id"] = str(d["id"])
        d["id"] = d["_id"]
        
    if not d.get("createdAt") and report.id:
        try:
            d["createdAt"] = report.id.generation_time.isoformat()
        except Exception:
            pass
    if not d.get("updatedAt") and d.get("createdAt"):
        d["updatedAt"] = d["createdAt"]

    # Policy Continuity: manual inputs are checked first (original start date and renewal break).
    # If not declared, evaluated across the session for multi-policy or computed on read.
    try:
        from app.services.multi_policy_orchestrator import continuity_for_report
        from app.services.coverage.policy_continuity import evaluate_policy_continuity, declared_policy_continuity

        pj = dict(d.get("policyJson") or {})
        rx = d.get("prescriptionJson") or {}

        # Ensure manual inputs from Policy database record are present in pj
        if report.policy_id:
            try:
                from app.models.policy import Policy
                from bson import ObjectId
                pol = await Policy.get(ObjectId(report.policy_id))
                if pol:
                    from app.services.coverage.policy_continuity import declared_policy_inputs
                    pj.update(declared_policy_inputs(pol))
            except Exception:
                pass

        declared = declared_policy_continuity(pj, rx)
        if declared:
            d["policyContinuity"] = declared
        else:
            session_continuity = await continuity_for_report(report)
            if session_continuity:
                d["policyContinuity"] = session_continuity
            elif not d.get("policyContinuity") and not str(d.get("overallStatus") or "").startswith("Invalid"):
                d["policyContinuity"] = evaluate_policy_continuity(
                    [{"policyId": report.policy_id, "policyJson": pj}],
                    rx,
                    current_policy_id=report.policy_id,
                )
    except Exception:
        pass

    return d


class BatchDeleteReportsRequest(BaseModel):
    ids: list[str]


@router.post("/batch-delete")
async def batch_delete_analysis_reports(
    body: BatchDeleteReportsRequest,
    current_user: dict = Depends(get_current_user)
):
    """Batch delete analysis reports."""
    from app.services.crud_service import CrudService
    from app.models.analysis_report import AnalysisReport

    service = CrudService(AnalysisReport, "AnalysisReport", [])
    result = await service.delete_batch(current_user["id"], body.ids)
    deleted_count = result.get("deletedCount", result.get("deleted", 0))
    skipped_count = result.get("skippedCount", len(body.ids) - deleted_count)
    return {
        "success": True,
        "deletedCount": deleted_count,
        "skippedCount": skipped_count,
        "message": f"Successfully deleted {deleted_count} report(s)"
    }


@router.delete("/{report_id}")
async def delete_analysis(
    report_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete an analysis report."""
    from app.services.crud_service import CrudService
    from app.models.analysis_report import AnalysisReport
    
    service = CrudService(AnalysisReport, "AnalysisReport", [])
    success = await service.delete(current_user["id"], report_id)
    if not success:
        raise HTTPException(status_code=404, detail="Analysis report not found")
        
    return {"success": True, "message": "Analysis report deleted successfully"}


