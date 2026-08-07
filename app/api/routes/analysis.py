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
    return {
        "success": True,
        "deletedCount": result["deletedCount"],
        "skippedCount": result["skippedCount"],
        "message": f"Successfully deleted {result['deletedCount']} report(s)"
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


