import io
import re
from datetime import datetime, date
from typing import Optional, Union, Any
from fastapi import APIRouter, Depends, HTTPException
from beanie import PydanticObjectId

from app.core.logging import logger
from app.middleware.auth import get_current_user
from app.models.policy import Policy
from app.services.storage.file_upload_service import FileUploadService

router = APIRouter(prefix="/policies", tags=["Policy Custom"])


_MONTHS_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _parse_date_to_iso(date_str: str) -> Optional[str]:
    """Parse date string into ISO format YYYY-MM-DD or return None."""
    if not date_str:
        return None
    s = date_str.strip()
    s = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)

    # 1. YYYY-MM-DD, YYYY/MM/DD, or YYYY.MM.DD
    m1 = re.match(r"^(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})$", s)
    if m1:
        y, m, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 2. DD/MM/YYYY, DD-MM-YYYY, or DD.MM.YYYY
    m2 = re.match(r"^(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2,4})$", s)
    if m2:
        d, m, y = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if y < 100:
            y += 2000
        if d > 12 and m <= 12:
            try:
                return datetime(y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                return None
        elif m > 12 and d <= 12:
            try:
                return datetime(y, d, m).strftime("%Y-%m-%d")
            except ValueError:
                return None
        else:
            # Default to DD/MM/YYYY for Indian insurance policies
            try:
                return datetime(y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                try:
                    return datetime(y, d, m).strftime("%Y-%m-%d")
                except ValueError:
                    return None

    # 3. DD Month YYYY (e.g., 01-Jan-2022, 15 April 2023)
    m3 = re.match(r"^(\d{1,2})[\s\-_/]+([A-Za-z]+)[\s\-_/,]+(\d{2,4})$", s)
    if m3:
        d, mon_str, y = int(m3.group(1)), m3.group(2).lower()[:3], int(m3.group(3))
        if y < 100:
            y += 2000
        if mon_str in _MONTHS_MAP:
            try:
                return datetime(y, _MONTHS_MAP[mon_str], d).strftime("%Y-%m-%d")
            except ValueError:
                return None

    # 4. Month DD, YYYY (e.g., January 15, 2023, Jan 15 2023)
    m4 = re.match(r"^([A-Za-z]+)[\s\-_/]+(\d{1,2})[\s\-_/,]+(\d{2,4})$", s)
    if m4:
        mon_str, d, y = m4.group(1).lower()[:3], int(m4.group(2)), int(m4.group(3))
        if y < 100:
            y += 2000
        if mon_str in _MONTHS_MAP:
            try:
                return datetime(y, _MONTHS_MAP[mon_str], d).strftime("%Y-%m-%d")
            except ValueError:
                return None

    return None


def _identify_start_date(text: str) -> Optional[str]:
    """
    Look for explicit policy start / inception / commencement dates in text:
    "Inception Date", "Policy Start Date", "Period of Insurance: From",
    "Risk Inception Date", "Valid From", "Cover Start Date", etc.
    Parse to ISO format YYYY-MM-DD.
    """
    if not text:
        return None

    date_regex = (
        r"(?:\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}|"
        r"\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}|"
        r"\d{1,2}(?:st|nd|rd|th)?[\s\-_/]+[A-Za-z]+[\s\-_/,]+\d{2,4}|"
        r"[A-Za-z]+[\s\-_/]+\d{1,2}(?:st|nd|rd|th)?[\s\-_/,]+\d{2,4})"
    )

    label_patterns = [
        r"period\s+of\s+insurance\s*[:\-]?(?:\s*from)?",
        r"policy\s+period\s*[:\-]?(?:\s*from)?",
        r"(?:risk\s+)?inception\s+date",
        r"policy\s+start\s+date",
        r"cover\s+start\s+date",
        r"(?:risk\s+)?commencement\s+date",
        r"valid\s+from",
        r"effective\s+from",
        r"coverage\s+from",
        r"start\s+date",
    ]

    for label in label_patterns:
        pattern = rf"(?:{label})[^\w\d\n\r]*.*?(?:from\s+)?(?:[a-zA-Z0-9\s,:]*?)({date_regex})"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw_date = match.group(1)
            parsed = _parse_date_to_iso(raw_date)
            if parsed:
                return parsed

    return None


def _identify_is_renewal(text: str) -> Optional[bool]:
    """
    Identify whether the text explicitly indicates a renewal or a fresh policy:
    - True if explicitly mentions renewal: "Renewal", "Renewed Policy", "Renewal of Policy",
      "Policy Year 2/3/etc.", "Previous Policy No".
    - False if explicitly mentions fresh/new policy: "Fresh Policy", "New Business", "First Year".
    - None if not mentioned or ambiguous.
    """
    if not text:
        return None

    fresh_patterns = [
        r"\bfresh\s+policy\b",
        r"\bnew\s+business\b",
        r"\bfirst\s+year\b",
        r"\b1st\s+year\b",
        r"\bpolicy\s+year\s*[:\-]?\s*0?1\b",
        r"\b(?:type|basis|transaction)\s*(?:of\s+policy)?\s*[:\-]?\s*new(?:\s+business)?\b",
    ]

    renewal_patterns = [
        r"\brenewed\s+policy\b",
        r"\brenewal\s+of\s+policy\b",
        r"\brenewal\s+of\b",
        r"\bpolicy\s+year\s*[:\-]?\s*([2-9]|\d{2,})\b",
        r"\b(?:2nd|3rd|[4-9]th|\d+th)\s+year\b",
        r"\b(?:type|basis|transaction)\s*(?:of\s+policy)?\s*[:\-]?\s*renewal\b",
        r"\b(?:fresh\s*/\s*renewal|new\s*/\s*renewal)\s*[:\-]?\s*renewal\b",
        r"(?<!non[-\s])\brenewal\s+policy\b",
        r"(?<!non[-\s])\brenewal\s+certificate\b",
        r"(?<!non[-\s])\brenewal\s+premium\b",
        r"(?<!non[-\s])\brenewal\b",
    ]

    has_prev_policy = False
    prev_match = re.search(
        r"\bprev(?:ious)?\s+policy\s*(?:no\.?|num(?:ber)?|#)?\s*[:\-]?\s*([A-Za-z0-9/\-]+)",
        text,
        re.IGNORECASE,
    )
    if prev_match:
        val = prev_match.group(1).strip().upper()
        if val not in ("NA", "N/A", "NIL", "NONE", "NOT", "NULL", "-"):
            has_prev_policy = True
    elif re.search(r"\bprev(?:ious)?\s+policy\s*(?:no\.?|num(?:ber)?|#)?\b", text, re.IGNORECASE):
        has_prev_policy = True

    filtered_text = re.sub(
        r"\b(?:non-renewal|notice\s+of\s+(?:non-)?renewal|terms\s+of\s+renewal|"
        r"renewal\s+clause|renewal\s+terms|subject\s+to\s+renewal|conditions?\s+of\s+renewal)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )

    has_renewal = has_prev_policy or any(re.search(p, filtered_text, re.IGNORECASE) for p in renewal_patterns)
    has_fresh = any(re.search(p, text, re.IGNORECASE) for p in fresh_patterns)

    prev_na_match = re.search(
        r"\bprev(?:ious)?\s+policy\s*(?:no\.?|num(?:ber)?|#)?\s*[:\-]?\s*(?:NA|N/A|NIL|NONE)\b",
        text,
        re.IGNORECASE,
    )
    if prev_na_match and not has_prev_policy:
        has_fresh = True

    if has_renewal and not has_fresh:
        return True
    if has_fresh and not has_renewal:
        return False
    return None


def _identify_renewed_without_break(text: str) -> Optional[bool]:
    """
    Look for explicit statements regarding continuous coverage / breaks:
    - True if continuous cover without break is explicitly stated.
    - False if break is explicitly mentioned.
    - None if not mentioned or ambiguous.
    """
    if not text:
        return None

    cont_patterns = [
        r"\bcontinuous\s+coverage\s+since\b",
        r"\brenewed\s+without\s+(?:any\s+|a\s+)?break\b",
        r"\bwithout\s+(?:any\s+|a\s+)?break(?:\s+in\s+(?:coverage|insurance))?\b",
        r"\bno\s+break\s+in\s+(?:coverage|insurance)\b",
        r"\bcontinuous\s+(?:coverage|cover|renewal)\b",
        r"\bcoverage\s+continuous\s+from\b",
        r"\binsured\s+with\s+us\s+since\b",
    ]

    break_patterns = [
        r"(?<!without\s)(?<!without any\s)(?<!without a\s)(?<!no\s)\bbreak\s+in\s+(?:insurance|coverage|continuity)\b",
        r"\brenewed\s+after\s+(?:a\s+)?break\b",
        r"\brenewed\s+with\s+(?:a\s+)?break\b",
        r"\bpolicy\s+lapsed\s+and\s+revived\b",
        r"(?<!no\s)\blapse\s+in\s+(?:coverage|insurance)\b",
        r"(?<!no\s)\bgap\s+in\s+(?:coverage|insurance)\b",
        r"\blapsed\s+policy\b",
    ]

    has_cont = any(re.search(p, text, re.IGNORECASE) for p in cont_patterns)
    has_break = any(re.search(p, text, re.IGNORECASE) for p in break_patterns)

    if has_cont and not has_break:
        return True
    if has_break and not has_cont:
        return False
    return None



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


@router.post("/{policy_id}/extract-details")
async def extract_policy_details(policy_id: str, current_user: dict = Depends(get_current_user)):
    """
    Extract policy details (isRenewal, startDate, renewedWithoutBreak)
    from an uploaded policy document.
    """
    fallback_response = {
        "success": True,
        "isRenewal": None,
        "startDate": None,
        "renewedWithoutBreak": None,
        "extractedFields": {
            "isRenewal": False,
            "startDate": False,
            "renewedWithoutBreak": False,
        },
    }

    try:
        # 1. Fetch Policy document by policy_id
        policy = None
        try:
            if PydanticObjectId.is_valid(policy_id):
                policy = await Policy.get(PydanticObjectId(policy_id))
        except Exception:
            policy = None

        if not policy:
            try:
                policy = await Policy.get(policy_id)
            except Exception:
                policy = None

        if not policy or policy.user_id != current_user["id"]:
            # Never read or reveal another user's policy.
            raise HTTPException(status_code=404, detail="Policy not found")

        # 2. Retrieve PDF buffer from GridFS if grid_fs_file_id is present
        pdf_buffer = None
        if policy.grid_fs_file_id:
            try:
                pdf_buffer = await FileUploadService.get_file_bytes(policy.grid_fs_file_id)
            except Exception as e:
                logger.warning(
                    f"[Policy Extract] Could not fetch GridFS buffer for policy {policy_id} "
                    f"(file {policy.grid_fs_file_id}): {e}"
                )

        # 3. Extract text from the PDF using existing PDF tools
        text = ""
        if pdf_buffer:
            try:
                from app.services.pdf.pdf_extractor import extract_text_from_pdf_buffer
                text = extract_text_from_pdf_buffer(pdf_buffer)
            except Exception as e:
                logger.warning(f"[Policy Extract] PyMuPDF extraction failed for policy {policy_id}: {e}")

            if not text or not text.strip():
                try:
                    import io
                    from pypdf import PdfReader
                    reader = PdfReader(io.BytesIO(pdf_buffer))
                    pages_text = [page.extract_text() or "" for page in reader.pages]
                    text = "\n".join(pages_text)
                except Exception as e:
                    logger.debug(f"[Policy Extract] pypdf fallback failed for policy {policy_id}: {e}")

        # Fallback to existing extracted_policy_text on policy model if available
        if (not text or not text.strip()) and policy.extracted_policy_text:
            text = policy.extracted_policy_text

        # If document has no readable text (e.g. scanned image-only PDF without OCR)
        if not text or len(text.strip()) < 10:
            return fallback_response

        # Cache extracted text if policy did not have it stored
        if not policy.extracted_policy_text and text:
            try:
                policy.extracted_policy_text = text
                await policy.save()
            except Exception as e:
                logger.debug(f"[Policy Extract] Could not cache extracted_policy_text on policy {policy_id}: {e}")

        # 4. Read the schedule's continuity facts with the same extraction the
        #    analysis uses, and keep them on the policy so the analysis reuses them.
        #    (Keyword matching alone mistook renewal clauses for "renewed" and the
        #    current period's start for the first start date.)
        from app.services.extraction.policy_continuity_extraction import ensure_continuity_fields

        stored = dict(policy.extracted_policy_json or {})
        if await ensure_continuity_fields(stored, text):
            try:
                policy.extracted_policy_json = stored
                await policy.save()
            except Exception as e:
                logger.debug(f"[Policy Extract] Could not cache continuity on policy {policy_id}: {e}")

        prefill = prefill_from_continuity(stored)
        is_renewal = prefill["isRenewal"]
        start_date = prefill["startDate"]
        renewed_without_break = prefill["renewedWithoutBreak"]

        # 5. Return JSON format
        return {
            "success": True,
            "isRenewal": is_renewal,
            "startDate": start_date,
            "renewedWithoutBreak": renewed_without_break,
            "extractedFields": {
                "isRenewal": is_renewal is not None,
                "startDate": start_date is not None,
                "renewedWithoutBreak": renewed_without_break is not None,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[Policy Extract] Unexpected error extracting details for policy {policy_id}: {e}",
            exc_info=True,
        )
        return fallback_response

