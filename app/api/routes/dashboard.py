"""
Dashboard and Reports API Routes.
"""

from fastapi import APIRouter, Depends

from app.middleware.auth import get_current_user
from app.models.analysis_report import AnalysisReport

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

from app.models.policy import Policy
from app.models.prescription import Prescription
from app.models.activity_log import ActivityLog

@router.get("")
async def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """Get aggregated statistics for the dashboard."""
    user_id = current_user["id"]
    
    # Run multiple queries concurrently (in Node this was Promise.all)
    # Beanie supports async queries which we can await sequentially, or use asyncio.gather
    import asyncio
    
    async def get_total_policies():
        return await Policy.find(Policy.user_id == user_id).count()
        
    async def get_total_prescriptions():
        return await Prescription.find(Prescription.user_id == user_id).count()
        
    async def get_total_reports():
        return await AnalysisReport.find(AnalysisReport.user_id == user_id).count()
        
    async def get_recent_activities():
        # Using native Motor for projection & sorting
        activities = await ActivityLog.find(ActivityLog.user_id == user_id).sort("-_id").limit(5).to_list()
        return [{"id": str(a.id), "action": a.action, "entityType": a.entity_type, "entityId": a.entity_id, "createdAt": a.id.generation_time} for a in activities]
        
    async def get_recent_analyses():
        reports = await AnalysisReport.find(AnalysisReport.user_id == user_id).sort("-_id").limit(5).to_list()
        return [{
            "id": str(r.id),
            "status": r.status,
            "overallStatus": r.overall_status,
            "dominanceScore": r.dominance_score,
            "processingTimeMs": r.processing_time_ms,
            "reportNumber": r.report_number,
            "createdAt": (r.created_at.isoformat() + "Z") if r.created_at else (r.id.generation_time.isoformat() if (r.id.generation_time.isoformat().endswith("Z") or "+" in r.id.generation_time.isoformat()) else r.id.generation_time.isoformat() + "Z")
        } for r in reports]

    results = await asyncio.gather(
        get_total_policies(),
        get_total_prescriptions(),
        get_total_reports(),
        get_recent_activities(),
        get_recent_analyses()
    )
    
    return {
        "totalPolicies": results[0],
        "totalPrescriptions": results[1],
        "totalAnalysisReports": results[2],
        "recentActivities": results[3],
        "recentAnalyses": results[4],
    }
