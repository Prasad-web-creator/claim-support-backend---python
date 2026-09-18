"""
Analysis routes — migrated from analysisController.js.
"""

import math
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from typing import Optional, List, Any
from pydantic import BaseModel, model_validator

from app.middleware.auth import get_current_user
from app.middleware.rate_limiter import limiter
from app.services.analysis_interactive_orchestrator import start_analysis_session, resume_analysis_session
from app.services.multi_policy_orchestrator import (
    start_multi_policy_analysis,
    resume_policy_analysis,
    retry_policy_analysis,
    get_multi_policy_session,
)
from app.core.config import get_settings
from app.models.analysis_report import AnalysisReport
from app.services.crud_service import CrudService

router = APIRouter(prefix="/analysis", tags=["Analysis"])


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


@router.post("/start")
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
        raise HTTPException(status_code=500, detail=str(e))

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


@router.post("/start-multi")
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
    """
    try:
        result = await start_multi_policy_analysis(
            user_id=current_user["id"],
            prescription_id=body.prescriptionPath,
            policy_ids=body.policyIds,
            background_tasks=background_tasks
        )
        return {"success": True, **result}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/multi/{session_id}/{policy_id}/answer")
@limiter.limit("5/minute")
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
            background_tasks=background_tasks
        )
        return {"success": True, **result}
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/multi/{session_id}/{policy_id}/retry")
@limiter.limit("5/minute")
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
            background_tasks=background_tasks
        )
        return {"success": True, **result}
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/{session_id}/answer")
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
        raise HTTPException(status_code=500, detail=str(e))


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

    # Ensure referenceComparison is populated even for legacy/pre-existing reports
    if not d.get("referenceComparison") or (isinstance(d.get("referenceComparison"), dict) and len(d["referenceComparison"]) == 0):
        try:
            from app.services.coverage.reference_benchmark_service import generate_reference_comparison
            ref_comp = generate_reference_comparison(
                policy_json=d.get("policyJson"),
                prescription_json=d.get("prescriptionJson"),
                coverage_analysis=d.get("coverageAnalysis")
            )
            d["referenceComparison"] = ref_comp
            # Persist to database so subsequent queries have it
            try:
                report.reference_comparison = ref_comp
                await report.save()
            except Exception:
                pass
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


