from datetime import datetime, date
from fastapi import APIRouter, Depends
from app.middleware.auth import get_current_user
from app.models.policy import Policy

router = APIRouter(prefix="/policies", tags=["Policy Custom"])


def _to_dd_mm_yyyy(val):
    if not val:
        return None
    if isinstance(val, (datetime, date)):
        return val.strftime("%d-%m-%Y")
    str_val = str(val).strip()
    # Try parsing common formats
    clean_val = str_val.split("T")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(clean_val, fmt)
            return dt.strftime("%d-%m-%Y")
        except ValueError:
            continue
    return str_val


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
    ).sort("-_id").to_list()
    
    # Format according to spec, returning holder name, start and end dates (DD-MM-YYYY)
    formatted_policies = []
    for p in policies:
        meta = p.metadata or {}
        p_json = p.extracted_policy_json or {}
        
        # Policy Holder Name
        holder_name = (
            getattr(p, "policy_holder_name", None)
            or meta.get("policy_holder_name")
            or meta.get("insured_person_name")
            or p_json.get("policyHolderName")
            or p_json.get("policyHolder")
            or p_json.get("insuredName")
        )
        if not holder_name and isinstance(p_json.get("insuredMembers"), list) and len(p_json["insuredMembers"]) > 0:
            m = p_json["insuredMembers"][0]
            if isinstance(m, dict):
                holder_name = m.get("name")
            elif isinstance(m, str):
                holder_name = m

        # Start Date
        raw_start = (
            p.policy_start_date
            or meta.get("policy_start_date")
            or p_json.get("policyStartDate")
            or p_json.get("startDate")
            or p_json.get("validFrom")
        )
        start_date = _to_dd_mm_yyyy(raw_start)

        # End / Expiry Date
        raw_end = (
            p.policy_end_date
            or meta.get("policy_expiry_date")
            or p_json.get("policyEndDate")
            or p_json.get("expiryDate")
            or p_json.get("validTo")
        )
        end_date = _to_dd_mm_yyyy(raw_end)

        formatted_policies.append({
            "id": str(p.id),
            "providerName": p.insurance_company or meta.get("provider_name") or p_json.get("insuranceCompany") or "---",
            "policyNumber": p.policy_number or meta.get("policy_number") or p_json.get("policyNumber") or "---",
            "policyType": p.policy_type or meta.get("policy_type") or p_json.get("policyType") or "---",
            "planName": p.policy_name or meta.get("plan_name") or p_json.get("policyName") or "---",
            "policyHolderName": holder_name or "---",
            "startDate": start_date or "---",
            "endDate": end_date or "---",
            "expiryDate": end_date or "---",
            "coverageAmount": p.coverage_amount or meta.get("sum_insured") or p_json.get("sumInsured"),
            "originalFileName": p.original_file_name or "---",
            "displayId": f"PCY{p.sequence_number:04d}" if getattr(p, "sequence_number", None) is not None else str(p.id)[:8].upper()
        })
        
    return {
        "success": True,
        "policies": formatted_policies
    }
