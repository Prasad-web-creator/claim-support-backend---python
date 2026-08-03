"""
Custom Policy routes.
"""

from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user
from app.models.policy import Policy

router = APIRouter(prefix="/policies", tags=["Policy Custom"])

@router.get("/summary")
async def get_policies_summary(current_user: dict = Depends(get_current_user)):
    """Get a lightweight list of the user's policies."""
    user_id = current_user["id"]
    
    # We only return policies that have successfully completed extraction
    # and are not deleted.
    policies = await Policy.find(
        Policy.user_id == user_id,
        Policy.is_deleted == False,
        Policy.processing_status == "completed"
    ).sort("-id").to_list()
    
    # Format according to spec, omitting heavy fields
    formatted_policies = []
    for p in policies:
        formatted_policies.append({
            "id": str(p.id),
            "providerName": p.insurance_company or "Unknown Provider",
            "policyNumber": p.policy_number or "Unknown Number",
            "policyType": p.policy_type or "Unknown Type",
            "expiryDate": p.policy_end_date.strftime("%Y-%m-%d") if p.policy_end_date else None,
            "originalFileName": p.original_file_name or "Unknown File",
            "displayId": f"PCY{p.sequence_number:04d}" if getattr(p, "sequence_number", None) is not None else str(p.id)[:8].upper()
        })
        
    return {
        "success": True,
        "policies": formatted_policies
    }
