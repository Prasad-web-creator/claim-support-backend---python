from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from beanie import PydanticObjectId

from app.core.logging import logger
from app.middleware.auth import get_current_user
from app.models.policy import Policy
from app.services.analysis_jobs import require_analysis_consent

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
    policies = await Policy.find(
        Policy.user_id == user_id,
        Policy.processing_status == "completed"
    ).sort("-_id").to_list()
    
    # Format according to spec, returning holder name, start and end dates (DD-MM-YYYY)
    formatted_policies = []
    for p in policies:
        p_json = p.extracted_policy_json or {}
        
        # Policy Holder Name
        holder_name = (
            getattr(p, "policy_holder_name", None)
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
            or p_json.get("policyStartDate")
            or p_json.get("startDate")
            or p_json.get("validFrom")
        )
        start_date = _to_dd_mm_yyyy(raw_start)

        # End / Expiry Date
        raw_end = (
            p.policy_end_date
            or p_json.get("policyEndDate")
            or p_json.get("expiryDate")
            or p_json.get("validTo")
        )
        end_date = _to_dd_mm_yyyy(raw_end)

        formatted_policies.append({
            "id": str(p.id),
            "providerName": p.insurance_company or p_json.get("insuranceCompany") or "---",
            "policyNumber": p.policy_number or p_json.get("policyNumber") or "---",
            "policyType": p.policy_type or p_json.get("policyType") or "---",
            "planName": p.policy_name or p_json.get("policyName") or "---",
            "policyHolderName": holder_name or "---",
            "startDate": start_date or "---",
            "endDate": end_date or "---",
            "expiryDate": end_date or "---",
            "coverageAmount": p.coverage_amount or p_json.get("coverageAmount") or p_json.get("sumInsured") or 0.0,
            "originalFileName": p.original_file_name or "---",
            "displayId": f"PCY{p.sequence_number:04d}" if getattr(p, "sequence_number", None) is not None else str(p.id)[:8].upper()
        })
        
    return {
        "success": True,
        "policies": formatted_policies
    }


def prefill_from_continuity(policy_json: dict) -> dict:
    """
    Upload-form answers taken from the same continuity reading the analysis uses
    to raise "new or renewed?" / "first start date?" questions, so a prefilled
    answer can never contradict the document and trigger those questions.

    A value is returned only when the document states it; otherwise None.
    """
    from app.services.analysis_interactive_orchestrator import (
        _as_date,
        _continuity_block,
        _document_earliest_start,
        _document_says_new,
        _meaningful,
    )

    block = _continuity_block(policy_json)

    is_renewal: Optional[bool] = None
    if block.get("isRenewal") is True or _meaningful(block.get("previousPolicyNumber")):
        is_renewal = True
    elif _document_says_new(policy_json) is not None:
        is_renewal = False

    start = None
    if is_renewal is True:
        # When the policy was FIRST taken (before renewals), as the document proves it.
        earliest = _document_earliest_start(policy_json)
        start = earliest[0] if earliest else None
    elif is_renewal is False:
        start = _as_date(block.get("policyStartDate") or policy_json.get("policyStartDate"))

    no_break = block.get("renewedWithoutBreak")
    renewed_without_break = no_break if is_renewal is True and isinstance(no_break, bool) else None

    return {
        "isRenewal": is_renewal,
        "startDate": start.isoformat() if start else None,
        "renewedWithoutBreak": renewed_without_break,
    }


_DETAIL_KEYS = (
    "insuranceCompany", "policyName", "policyNumber", "policyHolder",
    "policyStartDate", "policyEndDate", "coverageAmount",
)


def _upload_details_response(stored: dict, *, is_policy: bool) -> dict:
    """What the upload screen shows: the facts read from the document, and which ones are missing."""
    prefill = prefill_from_continuity(stored) if is_policy else {
        "isRenewal": None, "startDate": None, "renewedWithoutBreak": None,
    }
    details = {key: stored.get(key) or None for key in _DETAIL_KEYS} if is_policy else {}
    return {
        "success": True,
        "isPolicy": is_policy,
        "isRenewal": prefill["isRenewal"],
        "startDate": prefill["startDate"],
        "renewedWithoutBreak": prefill["renewedWithoutBreak"],
        "extractedFields": {
            "isRenewal": prefill["isRenewal"] is not None,
            "startDate": prefill["startDate"] is not None,
            "renewedWithoutBreak": prefill["renewedWithoutBreak"] is not None,
        },
        "details": details,
        # The current period's start, offered when the user says the policy is new.
        "hints": {"policyStartDate": stored.get("policyStartDate") if is_policy else None},
    }


@router.post("/{policy_id}/extract-details")
async def extract_policy_details(policy_id: str, request: Request, current_user: dict = Depends(get_current_user)):
    """
    Read an uploaded policy right after upload: schedule details plus the
    continuity answers (new/renewed, start date, renewed without a break).

    The result is stored on the policy, so the analysis reuses it instead of
    reading the document again. The app asks the user only for what is missing.
    """
    await require_analysis_consent(
        current_user["id"], request.headers.get("X-Analysis-Job-Id"), require_job=True
    )
    from app.services.analysis_interactive_orchestrator import (
        _process_document, apply_extracted_policy_identity, has_extracted_policy_details,
    )
    from app.services.extraction.policy_continuity_extraction import CONTINUITY_KEY, ensure_continuity_fields
    from app.services.extraction.policy_extraction import extract_policy_details as read_policy_document

    fallback_response = {**_upload_details_response({}, is_policy=False), "isPolicy": None}

    policy = None
    try:
        if PydanticObjectId.is_valid(policy_id):
            policy = await Policy.get(PydanticObjectId(policy_id))
    except Exception:
        policy = None
    if not policy or policy.user_id != current_user["id"]:
        # Never read or reveal another user's policy.
        raise HTTPException(status_code=404, detail="Policy not found")

    try:
        text = policy.extracted_policy_text or ""
        if not text.strip() and policy.grid_fs_file_id:
            # Same reader as the analysis, so page numbers in evidence stay correct.
            try:
                text = await _process_document(policy.grid_fs_file_id, current_user["id"], doc_label="Policy Document")
            except Exception as e:
                logger.warning("Policy text extraction failed ({})", type(e).__name__)
        if not text or len(text.strip()) < 10:
            return fallback_response

        stored = dict(policy.extracted_policy_json or {})
        if not has_extracted_policy_details(stored):
            result = await read_policy_document(text)
            if not result.get("isValid"):
                return _upload_details_response({}, is_policy=False)
            extracted = dict(result.get("extractedJson") or {})
            if CONTINUITY_KEY not in extracted and isinstance(stored.get(CONTINUITY_KEY), dict):
                extracted[CONTINUITY_KEY] = stored[CONTINUITY_KEY]
            stored = extracted
        await ensure_continuity_fields(stored, text)

        try:
            policy.extracted_policy_text = text
            policy.extracted_policy_json = stored
            if stored.get("policyHolder") and not policy.policy_holder_name:
                policy.policy_holder_name = stored["policyHolder"]
            apply_extracted_policy_identity(policy, stored)
            await policy.save()
        except Exception as e:
            logger.debug("Policy extraction cache write failed ({})", type(e).__name__)

        return _upload_details_response(stored, is_policy=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Policy detail extraction failed ({})", type(e).__name__)
        return fallback_response

