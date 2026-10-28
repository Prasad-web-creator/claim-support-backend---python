from typing import Awaitable, Callable, Optional
from datetime import datetime, timedelta
from fastapi import BackgroundTasks
import time
import asyncio
from beanie import PydanticObjectId as ObjectId
import json
import re
from app.core.logging import logger, log_section
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession, ClarificationQuestion, ClarificationAnswer
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.services.extraction.document_extraction import extract_text_from_document
from app.services.extraction.text_cleaning import clean_text
from app.services.extraction.policy_extraction import extract_policy_details
from app.services.extraction.prescription_extraction import extract_prescription_details
from app.services.extraction.json_validation import validate_extracted_json
from app.services.extraction.policy_continuity_extraction import CONTINUITY_KEY, ensure_continuity_fields
from app.services.coverage.business_rules import enforce_business_rules
from app.services.coverage.coverage_analysis import analyze_coverage
from app.services.coverage.policy_continuity import declared_policy_inputs
from app.services.coverage.report_generator import generate_report
from app.services.rule_engine.date_utils import parse_flexible_date
from app.services.llm.ai_client import AnalysisCostTracker, set_current_cost_tracker, get_current_cost_tracker
from app.services.analysis_orchestrator import _fetch_file_bytes_by_gridfs_id, _background_post_processing


# Names a policy record carries before its document has been read.
PLACEHOLDER_POLICY_NAMES = {"", "uploaded policy", "unknown", "n/a", "policy"}


def is_placeholder_policy_name(name) -> bool:
    """True when a stored policy name says nothing about the actual policy."""
    return str(name or "").strip().lower() in PLACEHOLDER_POLICY_NAMES


def _meaningful(value) -> str:
    """Extracted text worth showing, or an empty string."""
    text = str(value or "").strip()
    if not text or text.lower() in ("n/a", "na", "unknown", "not specified", "null", "none"):
        return ""
    return text


def apply_extracted_policy_identity(policy_doc, policy_json: dict) -> bool:
    """
    Fill a policy record's name/insurer/number from its extracted document.

    Only fills what is still a placeholder, so a name the user gave the policy
    themselves is never overwritten by an extraction. Returns whether anything
    changed.
    """
    if not policy_doc or not isinstance(policy_json, dict):
        return False

    changed = False

    extracted_name = _meaningful(policy_json.get("policyName"))
    if extracted_name and is_placeholder_policy_name(policy_doc.policy_name):
        policy_doc.policy_name = extracted_name[:255]
        changed = True

    insurer = _meaningful(policy_json.get("insuranceCompany"))
    if insurer and not _meaningful(policy_doc.insurance_company):
        policy_doc.insurance_company = insurer[:255]
        changed = True

    number = _meaningful(policy_json.get("policyNumber"))
    if number and not _meaningful(policy_doc.policy_number):
        policy_doc.policy_number = number[:255]
        changed = True

    return changed


async def _report_stage(on_stage: Optional[Callable[[str], Awaitable[None]]], stage: str) -> None:
    """
    Tell the caller a real pipeline step has begun.

    Progress reporting must never be able to break an analysis, so a failing
    callback is logged and swallowed.
    """
    if on_stage is None:
        return
    try:
        await on_stage(stage)
    except Exception as e:
        logger.warning(f"[Claim Analysis] Progress callback failed at stage '{stage}': {e}")


def _parse_and_format_date(date_val):
    if not date_val:
        return None, None
    parsed = None
    if isinstance(date_val, datetime):
        parsed = date_val.replace(tzinfo=None)
    else:
        try:
            from dateutil import parser
            parsed = parser.parse(str(date_val)).replace(tzinfo=None)
        except Exception:
            pass
        if not parsed:
            clean_str = str(date_val).strip()
            for fmt in (
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%d-%m-%Y",
                "%m/%d/%Y",
                "%Y/%m/%d",
                "%d.%m.%Y",
                "%d-%b-%Y",
                "%d-%B-%Y",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
            ):
                try:
                    parsed = datetime.strptime(clean_str.split("T")[0] if "T" not in fmt and "T" in clean_str else clean_str, fmt)
                    break
                except Exception:
                    continue
    if not parsed:
        # Dates as printed on documents: "01-Apr-2026 00:00 Hrs", "April 1, 2026", "1st April 2026"
        flexible = parse_flexible_date(date_val)
        if flexible:
            parsed = datetime(flexible.year, flexible.month, flexible.day)
    if parsed:
        return parsed, parsed.strftime("%d-%m-%Y")
    return None, None


def _as_date(value):
    parsed, _ = _parse_and_format_date(value)
    return parsed.date() if parsed else None


def _apply_declared_inception(policy_json: dict, policy_doc):
    """
    Put the waiting-period start the user declared for this policy into policy_json:
    the first inception date (before any renewals), or the date cover restarted after a
    break. Policy validity still uses the
    current period from the document. Returns the start date, or None when not declared.
    """
    if not policy_doc or not getattr(policy_doc, "inception_date", None):
        return None
    inception = _as_date(policy_doc.inception_date)
    if not inception:
        return None
    current_start = _as_date(policy_json.get("policyStartDate"))

    if getattr(policy_doc, "is_renewal", None) is False:
        start = inception
        basis = f"Based on your inputs, this is a new policy first taken on {inception:%d-%b-%Y}"
    elif getattr(policy_doc, "continuous_renewal", None) is False:
        restart = _as_date(getattr(policy_doc, "cover_restart_date", None))
        start = restart or current_start or inception
        basis = (f"Based on your inputs, cover restarted after a break on {start:%d-%b-%Y}"
                 if restart else f"Based on your inputs, the renewals had a break, so cover counts from {start:%d-%b-%Y}")
    else:
        start = inception
        basis = f"Based on your inputs, this policy was first taken on {inception:%d-%b-%Y} and renewed every year without a break"

    if current_start and start > current_start:
        # Cover cannot have started after the current period began.
        start = current_start
        basis = (f"The date you entered is after the current policy period starts, so waiting periods count "
                 f"from {current_start:%d-%b-%Y}")

    policy_json["waitingPeriodStartDate"] = start.isoformat()
    policy_json["waitingPeriodBasis"] = basis
    policy_json.update(declared_policy_inputs(policy_doc))
    policy_json["declaredInceptionDate"] = policy_json["inceptionDate"] = inception.isoformat()
    policy_json["continuousRenewal"] = getattr(policy_doc, "continuous_renewal", None)
    # Dates as plain YYYY-MM-DD, like the inception date.
    for key in ("coverRestartDate", "restartDate", "breakStartDate"):
        parsed = _as_date(policy_json.get(key)) if policy_json.get(key) else None
        if parsed:
            policy_json[key] = parsed.isoformat()
    return start


def _inception_conflict_question(policy_json: dict, declared_start) -> Optional[dict]:
    """Ask only when the policy document itself states a different continuous-cover start."""
    block = policy_json.get(CONTINUITY_KEY) if isinstance(policy_json.get(CONTINUITY_KEY), dict) else {}
    stated = _as_date(policy_json.get("continuousCoverageSince") or block.get("continuousCoverageSince"))
    if not stated or stated == declared_start:
        return None
    mine, theirs = f"{declared_start:%d-%m-%Y}", f"{stated:%d-%m-%Y}"
    return {
        "id": "policy_inception_date_conflict",
        "category": "eligibility",
        "title": "Policy Inception Date",
        "question": (f"The policy document states continuous cover since {theirs}, but you entered {mine}. "
                     f"Which date should be used for waiting periods?"),
        "type": "single_choice",
        "required": True,
        "options": [f"Use my entered date ({mine})", f"Use policy document date ({theirs})"],
        "reason": "Waiting periods are counted from the date cover first started without a break.",
    }


def _date_from_answer(answer) -> Optional[datetime]:
    """A date from a clarification answer: a picked date ("14-Oct-2026"), an option containing one, or today/yesterday."""
    text = str(answer or "").strip()
    if not text:
        return None
    parsed, _ = _parse_and_format_date(text)
    if parsed:
        return parsed
    import re
    match = re.search(r"\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{4}-\d{2}-\d{2}", text)
    if match:
        parsed, _ = _parse_and_format_date(match.group(0))
        if parsed:
            return parsed
    lower = text.lower()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if "yesterday" in lower:
        return today - timedelta(days=1)
    if "today" in lower:
        return today
    return None

class PrescriptionContext:
    """
    Resolved prescription inputs, shared by the single-policy and multi-policy flows.

    Holds everything needed to turn a `prescription_id` (which may be a Prescription
    id, a StoredFile id, or raw manual text) into cleaned text + normalized JSON.
    """

    def __init__(self, prescription_id: str, rx_doc=None, is_manual: bool = False,
                 manual_text: Optional[str] = None, target_file_id: Optional[str] = None):
        self.prescription_id = prescription_id
        self.rx_doc = rx_doc
        self.is_manual = is_manual
        self.manual_text = manual_text
        self.target_file_id = target_file_id
        self.text: str = ""
        self.json: dict = {}


class PolicyContext:
    """Resolved policy inputs for one policy (document record + any cached extraction)."""

    def __init__(self, policy_doc=None, policy_file_id: Optional[str] = None,
                 cached_text: str = "", cached_json: Optional[dict] = None):
        self.policy_doc = policy_doc
        self.policy_file_id = policy_file_id
        self.cached_text = cached_text
        self.cached_json = cached_json or None
        self.text: str = ""

    @property
    def has_cached_text(self) -> bool:
        return bool(self.cached_text)

    @property
    def identifier(self) -> str:
        return str(self.policy_doc.id) if self.policy_doc else (self.policy_file_id or "")


async def _process_document(doc_id, user_id: str, doc_label: str = "Document") -> str:
    """Fetch a stored file and extract + clean its text."""
    logger.debug(f"[Claim Analysis] Fetching file bytes from storage for {doc_label} (ID: {doc_id})...")
    bytes_data, mime = await _fetch_file_bytes_by_gridfs_id(doc_id, user_id)
    raw_text = await extract_text_from_document(bytes_data, mime, doc_label=doc_label)
    return clean_text(raw_text)


async def _resolve_prescription_context(user_id: str, prescription_id: str) -> PrescriptionContext:
    """Resolve which prescription record / file / manual text a request refers to."""
    rx_doc = None
    if prescription_id:
        try:
            if len(str(prescription_id)) == 24:
                rx_doc = await Prescription.get(ObjectId(prescription_id))
        except Exception:
            rx_doc = None

    ctx = PrescriptionContext(prescription_id=prescription_id, target_file_id=prescription_id)
    if not rx_doc and prescription_id and not re.fullmatch(r"[0-9a-fA-F]{24}", str(prescription_id).strip()):
        # Not a record or file id: the app passed the typed prescription itself
        # (e.g. "hernia" when saving the record failed). Treat it as manual text.
        ctx.is_manual = True
        ctx.manual_text = str(prescription_id).strip()
        ctx.target_file_id = None
        return ctx
    if rx_doc:
        ctx.rx_doc = rx_doc
        if rx_doc.is_manual:
            ctx.is_manual = True
            ctx.manual_text = rx_doc.extracted_prescription_text or ""
        elif rx_doc.grid_fs_file_id:
            ctx.target_file_id = rx_doc.grid_fs_file_id
    return ctx


async def _load_prescription_text(ctx: PrescriptionContext, user_id: str) -> str:
    """Extract (or read back manual) prescription text. Unchanged single-policy behaviour."""
    if ctx.is_manual and ctx.manual_text:
        logger.debug("[Claim Analysis] Prescription is self-entered manual text by user.")
        return clean_text(ctx.manual_text)
    try:
        return await _process_document(ctx.target_file_id, user_id, doc_label="Prescription Document")
    except Exception as e:
        # If target_file_id is raw manual text directly passed
        if ctx.prescription_id and len(ctx.prescription_id) > 10 and not ctx.prescription_id.isalnum():
            return clean_text(ctx.prescription_id)
        raise e


async def _resolve_policy_context(
    user_id: str,
    policy_doc_id: Optional[str] = None,
    policy_file_id: Optional[str] = None,
) -> PolicyContext:
    """
    Resolve a policy and its cached extraction. Enforces ownership for stored policies.
    """
    if policy_doc_id:
        policy_doc = await Policy.get(ObjectId(policy_doc_id))
        if not policy_doc or policy_doc.user_id != user_id:
            raise ValueError("Invalid Policy ID or unauthorized")
        if policy_doc.extracted_policy_text:
            logger.info(
                f"[Document] Using cached text for Policy {policy_doc.id} ({len(policy_doc.extracted_policy_text)} chars)"
            )
            return PolicyContext(
                policy_doc=policy_doc,
                cached_text=policy_doc.extracted_policy_text,
                cached_json=policy_doc.extracted_policy_json or {},
            )
        return PolicyContext(policy_doc=policy_doc)
    return PolicyContext(policy_file_id=policy_file_id)


OUTPATIENT_TERMS = ("opd", "outpatient", "out-patient", "out patient", "clinic visit", "no admission")


def _apply_manual_prescription_defaults(prescription_json: dict, rx_text: str) -> dict:
    """
    Tag self-entered prescriptions. They are a check before treatment ("if I go to hospital
    for this today, is it covered?"), so the treatment date is always today and the case is
    evaluated as a hospital admission unless the text says out-patient.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    prescription_json["isManual"] = True
    prescription_json["prescriptionSource"] = "Self-entered Prescription"
    prescription_json["manualText"] = rx_text
    prescription_json["visitDate"] = today_str
    prescription_json["consultationDate"] = today_str
    if any(t in (rx_text or "").lower() for t in OUTPATIENT_TERMS):
        prescription_json["hospitalizationRequired"] = False
    else:
        prescription_json["hospitalizationRequired"] = True
        prescription_json["admissionAssumed"] = True
    return prescription_json


async def _persist_prescription_extraction(
    ctx: PrescriptionContext,
    user_id: str,
    rx_text: str,
    prescription_json: dict,
    fallback_original_name: str = "",
) -> Optional[str]:
    """
    Persist extracted prescription text & JSON for instant reuse.
    Returns the prescription id when a new Prescription record was created.
    """
    rx_doc = ctx.rx_doc
    if rx_doc:
        rx_doc.extracted_prescription_text = rx_text or ""
        rx_doc.extracted_prescription_json = prescription_json or {}
        if ctx.is_manual:
            rx_doc.is_manual = True
        if prescription_json.get("patientName"):
            rx_doc.patient_name = str(prescription_json.get("patientName"))
        if prescription_json.get("doctorName"):
            rx_doc.doctor_name = str(prescription_json.get("doctorName"))
        if prescription_json.get("hospitalName"):
            rx_doc.hospital_name = str(prescription_json.get("hospitalName"))
        if prescription_json.get("diagnosis"):
            rx_doc.diagnosis = str(prescription_json.get("diagnosis"))
        if prescription_json.get("prescriptionNumber"):
            rx_doc.prescription_number = str(prescription_json.get("prescriptionNumber"))
        if prescription_json.get("visitDate") or prescription_json.get("consultationDate"):
            rx_doc.visit_date = prescription_json.get("visitDate") or prescription_json.get("consultationDate")
        rx_doc.processing_status = "completed"
        await rx_doc.save()
        logger.info(f"[Claim Analysis] Saved extracted prescription text & JSON to prescription {rx_doc.id}")
        return None

    if ctx.target_file_id:
        try:
            new_rx_doc = Prescription(
                user_id=user_id,
                hospital_name=str(prescription_json.get("hospitalName") or ""),
                doctor_name=str(prescription_json.get("doctorName") or ""),
                patient_name=str(prescription_json.get("patientName") or ""),
                prescription_number=str(prescription_json.get("prescriptionNumber") or ""),
                visit_date=prescription_json.get("visitDate") or prescription_json.get("consultationDate") or "",
                diagnosis=str(prescription_json.get("diagnosis") or ""),
                is_manual=ctx.is_manual,
                grid_fs_file_id="" if ctx.is_manual else str(ctx.target_file_id),
                original_file_name=fallback_original_name or "",
                extracted_prescription_text=rx_text or "",
                extracted_prescription_json=prescription_json or {},
                processing_status="completed"
            )
            await new_rx_doc.insert()
            ctx.rx_doc = new_rx_doc
            logger.info(f"[Claim Analysis] Saved prescription {new_rx_doc.id}")
            return str(new_rx_doc.id)
        except Exception as rx_err:
            logger.warning(f"[Claim Analysis] Could not save prescription record: {rx_err}")
    return None


def _build_invalid_document_report(
    validation: dict,
    policy_json: dict,
    prescription_json: dict,
    is_manual_rx: bool,
    processing_time_ms: int,
) -> dict:
    """Build (but never persist) the report returned when a document fails validation."""
    is_policy_valid = validation.get("isPolicyValid", True)
    is_rx_valid = validation.get("isPrescriptionValid", True)

    p_reason = ""
    rx_reason = ""
    if not is_policy_valid:
        p_reason = "The uploaded policy document contains no recognizable insurance policy clauses, covered treatments, benefit rules, or insurance terms."
    if not is_rx_valid:
        if is_manual_rx:
            rx_reason = "The self-entered text contains no recognizable medical details (no diagnosis, symptoms, diseases, medicines, or medical tests)."
        else:
            rx_reason = "The uploaded document contains no valid diagnosis, medicines, medical tests, procedures, or symptoms."

    if not is_policy_valid and not is_rx_valid:
        inv_status = "Invalid Policy and Prescription"
    elif not is_policy_valid:
        inv_status = "Invalid Policy"
    else:
        inv_status = "Invalid Prescription"

    return generate_report(
        policy_json=policy_json,
        prescription_json=prescription_json,
        business_rule_results={},
        coverage_analysis={
            "documentValidity": {
                "policyValid": is_policy_valid,
                "prescriptionValid": is_rx_valid,
                "isPolicyValid": is_policy_valid,
                "isPrescriptionValid": is_rx_valid,
                "policyInvalidReason": p_reason,
                "prescriptionInvalidReason": rx_reason,
                "errors": validation.get("errors", [])
            },
            "overallStatus": inv_status,
            "comparison": []
        },
        processing_time_ms=processing_time_ms
    )


async def run_single_policy_analysis(
    session: AnalysisSession,
    *,
    user_id: str,
    validation: dict,
    policy_ctx: PolicyContext,
    policy_text: str,
    is_manual_rx: bool,
    start_time: float,
    background_tasks: Optional[BackgroundTasks] = None,
    on_stage: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Reusable core: validated policy + prescription JSON → decision for ONE policy.

    `on_stage`, when supplied, is awaited as each real step begins
    ("rule_engine", "coverage_analysis", "report"), letting a caller record
    progress that matches the work actually done.

    Runs document-validity gating, the policy start date conflict check, the
    deterministic rule engine and the LLM coverage analysis for exactly one
    policy. Used by both `start_analysis_session` (single policy) and the
    multi-policy orchestrator (once per selected policy).
    """
    policy_json = validation["validatedPolicyJson"]
    prescription_json = validation["validatedPrescriptionJson"]
    policy_doc = policy_ctx.policy_doc

    # Cache the policy extraction on the Policy record for instant reuse next time
    if policy_doc and (not policy_doc.extracted_policy_text or not policy_doc.extracted_policy_json):
        policy_doc.extracted_policy_text = policy_text
        policy_doc.extracted_policy_json = policy_json
        if policy_json.get("policyHolder") and not policy_doc.policy_holder_name:
            policy_doc.policy_holder_name = policy_json.get("policyHolder")
        # A freshly uploaded policy is stored under a placeholder name because
        # nothing is known about it yet. Now that the document has been read,
        # give it its real identity so every later screen can name it.
        apply_extracted_policy_identity(policy_doc, policy_json)
        await policy_doc.save()
        logger.info(f"[Claim Analysis] Cached extracted text for policy {policy_doc.id}")

    session.policy_json = policy_json
    session.prescription_json = prescription_json

    # Early check for document validity
    is_policy_valid = validation.get("isPolicyValid", True)
    is_rx_valid = validation.get("isPrescriptionValid", True)

    if not is_policy_valid or not is_rx_valid:
        logger.warning(
            f"[Claim Analysis] Document validation failed for policy {session.policy_id}: "
            f"policyValid={is_policy_valid}, prescriptionValid={is_rx_valid}"
        )
        extraction_time_ms = int((time.time() - start_time) * 1000)
        session.extraction_time_ms = extraction_time_ms
        session.accumulated_processing_time_ms = extraction_time_ms
        session.status = "completed"
        await session.save()

        final_report = _build_invalid_document_report(
            validation=validation,
            policy_json=policy_json,
            prescription_json=prescription_json,
            is_manual_rx=is_manual_rx,
            processing_time_ms=extraction_time_ms,
        )

        # Do NOT store invalid coverage summary report in DB as per user requirement
        final_report["status"] = "complete"
        return final_report

    # Policies extracted before the continuity check existed are read for their
    # coverage-history facts once; the result is cached on the Policy record.
    if await ensure_continuity_fields(policy_json, policy_text) and policy_doc:
        stored = dict(policy_doc.extracted_policy_json or {})
        stored[CONTINUITY_KEY] = policy_json[CONTINUITY_KEY]
        policy_doc.extracted_policy_json = stored
        await policy_doc.save()

    # Waiting periods run from the inception date the user declared (first start,
    # before any renewals). The user's manual inputs are authoritative and not overridden by the document.
    declared_start = _apply_declared_inception(policy_json, policy_doc)

    # Policies saved before the inception question existed: the start date the user
    # entered is checked against the document's current period start.
    user_entered_start_date = None
    if declared_start and getattr(policy_doc, "is_renewal", None) is False:
        # A new policy starts on the date printed on it, so a different entered date is asked
        # about. A renewed policy's first start is naturally earlier, so it is not.
        user_entered_start_date = policy_doc.inception_date
    elif declared_start:
        pass
    elif policy_doc and policy_doc.policy_start_date:
        user_entered_start_date = policy_doc.policy_start_date
    elif policy_ctx.policy_file_id:
        try:
            p_by_file = await Policy.find_one(
                Policy.grid_fs_file_id == policy_ctx.policy_file_id,
                Policy.user_id == user_id,
            )
            if p_by_file and p_by_file.policy_start_date:
                user_entered_start_date = p_by_file.policy_start_date
        except Exception:
            pass

    user_dt, user_disp = _parse_and_format_date(user_entered_start_date)
    doc_dt, doc_disp = _parse_and_format_date(policy_json.get("policyStartDate"))

    if user_dt and doc_dt and user_dt.date() != doc_dt.date():
        logger.info(f"[Claim Analysis] Policy start date conflict: user entered {user_disp}, doc extracted {doc_disp}")

        question_data = {
            "id": "policy_start_date_conflict",
            "category": "eligibility",
            "title": "Policy Start Date Conflict",
            "question": f"Policy mentioned start date is {doc_disp}, but you mentioned {user_disp}. Can I proceed with your option?",
            "type": "single_choice",
            "required": True,
            "options": [
                f"Use my entered date ({user_disp})",
                f"Use policy document date ({doc_disp})"
            ],
            "reason": "A conflict was found between the start date you entered and the start date extracted from the uploaded policy document."
        }

        session.questions.append(ClarificationQuestion(
            id=question_data["id"],
            title=question_data["title"],
            question=question_data["question"],
            type=question_data["type"],
            category=question_data["category"],
            required=question_data["required"],
            options=question_data["options"],
            reason=question_data["reason"]
        ))

        session.status = "waiting_for_user"
        await session.save()

        return {
            "status": "needs_clarification",
            "sessionId": str(session.id),
            "questions": [question_data]
        }
    elif user_dt and not doc_dt:
        policy_json["policyStartDate"] = user_dt.strftime("%Y-%m-%d")
    elif user_dt and doc_dt and user_dt.date() == doc_dt.date():
        policy_json["policyStartDate"] = user_dt.strftime("%Y-%m-%d")

    log_section("Rule Engine & Eligibility")
    logger.info("[Rule Engine] Evaluating policy rules, waiting periods, and exclusions...")
    await _report_stage(on_stage, "rule_engine")
    br_results = enforce_business_rules(policy_json, prescription_json, policy_text=policy_text)
    session.business_rules = br_results
    det_res = br_results.get("deterministicResult") or {}
    det_status = det_res.get("status") or ("ELIGIBLE" if br_results.get("overallEligible") else "NOT_ELIGIBLE")
    det_reason = det_res.get("reasonCode") or ""
    logger.info(f"[Rule Engine] Decision: {det_status} ({det_reason})")

    # Track extraction & business rule processing time
    extraction_time_ms = int((time.time() - start_time) * 1000)
    session.extraction_time_ms = extraction_time_ms
    session.accumulated_processing_time_ms = extraction_time_ms
    session.status = "analyzing"
    await session.save()

    return await _run_llm_analysis(
        session, policy_json, prescription_json, br_results, background_tasks, on_stage=on_stage
    )


async def start_analysis_session(
    user_id: str,
    prescription_id: str,
    policy_file_id: Optional[str] = None,
    policy_doc_id: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None
) -> dict:
    start_time = time.time()
    log_section("Claim Analysis Started")
    logger.info(f"[Claim Analysis] Started session for User: {user_id}")

    session = AnalysisSession(
        user_id=user_id,
        policy_id=policy_doc_id or policy_file_id,
        prescription_id=prescription_id,
        status="extracting"
    )
    await session.insert()

    tracker = AnalysisCostTracker(session_id=str(session.id))
    set_current_cost_tracker(tracker)

    try:
        rx_ctx = await _resolve_prescription_context(user_id, prescription_id)

        log_section("Document Processing")
        policy_ctx = await _resolve_policy_context(
            user_id, policy_doc_id=policy_doc_id, policy_file_id=policy_file_id
        )

        if policy_ctx.has_cached_text:
            policy_text = policy_ctx.cached_text
            existing_policy_json = policy_ctx.cached_json
            rx_text = await _load_prescription_text(rx_ctx, user_id)
        else:
            logger.info("[Document] Extracting text from policy and prescription documents...")
            existing_policy_json = None
            policy_source_id = (
                policy_ctx.policy_doc.grid_fs_file_id if policy_ctx.policy_doc else policy_file_id
            )
            policy_text, rx_text = await asyncio.gather(
                _process_document(policy_source_id, user_id, doc_label="Policy Document"),
                _load_prescription_text(rx_ctx, user_id)
            )

        policy_ctx.text = policy_text
        session.policy_text = policy_text
        session.prescription_text = rx_text

        if existing_policy_json:
            log_section("Prescription Extraction")
            policy_data = {"extractedJson": existing_policy_json}
            rx_data = await extract_prescription_details(rx_text)
        else:
            log_section("Policy & Prescription Extraction")
            policy_data, rx_data = await asyncio.gather(
                extract_policy_details(policy_text),
                extract_prescription_details(rx_text)
            )

        validation = validate_extracted_json(
            policy_data.get("extractedJson"),
            rx_data.get("extractedJson")
        )

        prescription_json = validation["validatedPrescriptionJson"]

        if rx_ctx.is_manual:
            _apply_manual_prescription_defaults(prescription_json, rx_text)

        # Always persist extracted text and JSON on documents in MongoDB for instant reuse
        new_rx_id = await _persist_prescription_extraction(
            rx_ctx,
            user_id=user_id,
            rx_text=rx_text,
            prescription_json=prescription_json,
            fallback_original_name=session.prescription_id or "",
        )
        if new_rx_id:
            session.prescription_id = new_rx_id

        return await run_single_policy_analysis(
            session,
            user_id=user_id,
            validation=validation,
            policy_ctx=policy_ctx,
            policy_text=policy_text,
            is_manual_rx=rx_ctx.is_manual,
            start_time=start_time,
            background_tasks=background_tasks,
        )

    except Exception as e:
        logger.error(f"[Claim Analysis] Session {session.id} failed: {e}")
        session.status = "failed"
        await session.save()
        raise e

def _manual_review_result(policy_json: dict, prescription_json: dict, br_results: dict, reason: str) -> dict:
    """A complete Coverage Summary for an undecided case, so the app never opens an empty screen."""
    report = generate_report(
        policy_json=policy_json or {},
        prescription_json=prescription_json or {},
        business_rule_results=br_results or {},
        coverage_analysis={"comparison": [], "summary": reason},
        processing_time_ms=0,
    )
    report["status"] = "manual_review_required"
    report["reason"] = reason
    return report


async def _run_llm_analysis(
    session: AnalysisSession,
    policy_json: dict,
    prescription_json: dict,
    br_results: dict,
    background_tasks: Optional[BackgroundTasks],
    on_stage: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    from app.models.analysis_audit_log import AnalysisAuditLog
    llm_start_time = time.time()
    
    session.round_count += 1
    await session.save()

    if session.round_count > 3:
        logger.warning(f"[Claim Analysis] Session {session.id} exceeded max rounds. Forcing manual review.")
        session.status = "manual_review_required"
        await session.save()
        return _manual_review_result(
            policy_json, prescription_json, br_results,
            "The information provided was not enough to decide automatically. This case needs manual review.",
        )

    # Format clarification history — match by both field name and alias
    clarification_history = []
    for q in session.questions:
        # Support both `question_id` and `questionId` field name due to Pydantic alias
        answer_obj = next(
            (a for a in session.answers if getattr(a, 'question_id', None) == q.id or getattr(a, 'questionId', None) == q.id),
            None
        )
        if answer_obj:
            clarification_history.append({
                "Question_ID": q.id,
                "Title": q.title,
                "Question": q.question,
                "User_Answer": answer_obj.answer
            })
    
    log_section("AI Analysis")
    if clarification_history:
        logger.info(f"[AI Analysis] Evaluating with {len(clarification_history)} clarification answers (Round {session.round_count})...")
    else:
        logger.info(f"[AI Analysis] Evaluating coverage and generating explanation with Gemini (Round {session.round_count})...")

    coverage_analysis = await analyze_coverage(
        policy_text=session.policy_text or "",
        prescription_text=session.prescription_text or "",
        business_rule_results=br_results,
        prescription_json=prescription_json,
        clarification_history=clarification_history,
        policy_json=session.policy_json or {},
        policy_id=session.policy_id or "",
        # Reports "evidence_retrieval" then "coverage_analysis" from inside, so
        # the longest part of the pipeline is not one silent block.
        on_stage=lambda stage: _report_stage(on_stage, stage),
    )

    llm_round_duration_ms = int((time.time() - llm_start_time) * 1000)
    extraction_time = getattr(session, "extraction_time_ms", None) or session.accumulated_processing_time_ms or 0
    total_processing_time = extraction_time + llm_round_duration_ms

    next_action = coverage_analysis.get("next_action", "manual_review")
    confidence = coverage_analysis.get("confidence", 0)
    
    # Audit log
    audit_log = AnalysisAuditLog(
        session_id=str(session.id),
        analysis_round=session.round_count,
        prompt_version="2.1.0",
        prescription_id=session.prescription_id,
        policy_id=session.policy_id,
        clarification_context={"history": clarification_history},
        llm_response=coverage_analysis,
        confidence_score=confidence,
        final_decision=next_action
    )

    if next_action == "ask_questions":
        new_questions = coverage_analysis.get("questions", [])
        filtered_questions = []
        for q_data in new_questions:
            q_id = q_data.get("id")
            
            # Validation
            if not q_id or not q_data.get("title") or not q_data.get("question"):
                continue
            
            # Deduplication
            if not any(q.id == q_id for q in session.questions):
                if len(session.questions) + len(filtered_questions) >= 10:
                    logger.warning(f"[Claim Analysis] Session {session.id} reached max 10 questions.")
                    break
                
                session.questions.append(ClarificationQuestion(
                    id=q_id,
                    title=q_data.get("title", ""),
                    question=q_data.get("question", ""),
                    type=q_data.get("type", "text"),
                    category=q_data.get("category"),
                    required=q_data.get("required", True),
                    options=q_data.get("options", []),
                    reason=q_data.get("reason", "")
                ))
                filtered_questions.append(q_data)
        
        audit_log.generated_questions = filtered_questions
        await audit_log.insert()

        if not filtered_questions:
            logger.warning("[Claim Analysis] LLM returned duplicate or invalid questions. Forcing manual review.")
            session.status = "manual_review_required"
            await session.save()
            return _manual_review_result(
                policy_json, prescription_json, br_results,
                "Coverage could not be decided automatically from the information provided. This case needs manual review.",
            )

        session.status = "waiting_for_user"
        tracker = get_current_cost_tracker()
        if tracker:
            session.cost_steps = tracker.to_dict_list()
        await session.save()
        
        return {
            "status": "needs_clarification",
            "sessionId": str(session.id),
            "questions": filtered_questions
        }

    # Otherwise complete (generate_report or manual_review)
    await _report_stage(on_stage, "report")
    session.accumulated_processing_time_ms = total_processing_time
    session.status = "completed" if next_action == "generate_report" else "manual_review_required"
    await session.save()
    
    await audit_log.insert()
    
    analysis_data = coverage_analysis.get("analysis", coverage_analysis)
    
    final_report = generate_report(
        policy_json=policy_json,
        prescription_json=prescription_json,
        business_rule_results=br_results,
        coverage_analysis=analysis_data,
        processing_time_ms=total_processing_time
    )

    if str(final_report.get("overallStatus", "")).startswith("Invalid"):
        logger.info("[Claim Analysis] Invalid report status; skipping DB insertion as requested.")
        return final_report

    report = AnalysisReport(
        user_id=session.user_id,
        policy_id=session.policy_id,
        prescription_id=session.prescription_id,
        status="completed" if next_action == "generate_report" else "manual_review_required",
        analysis_version="2.1.0",
        model="gemini-2.5-flash",
        model_version="2.5-flash",
        prompt_version="1.2.0",
        rule_engine_version="2.0.0-deterministic",
        knowledge_base_version="1.0.0",
        dataset_version="1.0.0",
        policy_text=session.policy_text,
        prescription_text=session.prescription_text,
        policy_json=policy_json,
        prescription_json=prescription_json,
        business_rules=br_results,
        coverage_analysis=analysis_data,
        document_validity=final_report.get("documentValidity"),
        overall_status=final_report.get("overallStatus"),
        dominance_score=final_report.get("dominanceScore"),
        coverage_breakdown=final_report.get("coverageBreakdown"),
        summary=final_report.get("summary"),
        comparison=final_report.get("comparison"),
        processing_time_ms=total_processing_time,
        reference_comparison=final_report.get("referenceComparison") or {},
        policy_continuity=final_report.get("policyContinuity") or {},
        major_terms=final_report.get("majorTerms") or [],

        decision_type="Automatic" if next_action == "generate_report" else "Manual Review",
        confidence_score=confidence,
        policy_clauses_used=analysis_data.get("policyClausesUsed", []),
        prescription_evidence=analysis_data.get("prescriptionEvidence", []),
        clarification_answers_used=clarification_history,
        session_id=str(session.id),
        parent_session_id=session.parent_session_id or "",
        error_message=coverage_analysis.get("reason") if next_action == "manual_review" else None
    )
    await report.insert()
    log_section("Claim Decision Summary")
    logger.info(f"  - Report Number:   #{report.report_number} | ID: {report.id}")
    logger.info(f"  - Decision:        {report.overall_status} ({report.decision_type})")
    logger.info(f"  - Dominance Score: {report.dominance_score}%")
    logger.info(f"  - Processing Time: {total_processing_time}ms ({total_processing_time/1000.0:.2f}s)")
    logger.info(f"  - Session ID:      {session.id}")
    if session.parent_session_id:
        logger.info(f"  - Parent Session:  {session.parent_session_id} | Policy: {session.policy_id}")

    tracker = get_current_cost_tracker()
    if tracker:
        session.cost_steps = tracker.to_dict_list()
        tracker.report_number = report.report_number
        tracker.print_summary_table(doc_summary=f"End-to-End Pipeline ({report.overall_status})")
    
    session.report_id = str(report.id)
    await session.save()

    if background_tasks:
        background_tasks.add_task(
            _background_post_processing,
            user_id=session.user_id,
            report_id=str(report.id),
            policy_doc_id=session.policy_id if len(session.policy_id or "") > 20 else None,
            policy_file_id=None, 
            prescription_id=session.prescription_id,
            skip_prescription_metadata=bool(session.parent_session_id)
        )
    else:
        await _background_post_processing(
            user_id=session.user_id,
            report_id=str(report.id),
            policy_doc_id=session.policy_id if len(session.policy_id or "") > 20 else None,
            policy_file_id=None,
            prescription_id=session.prescription_id,
            skip_prescription_metadata=bool(session.parent_session_id)
        )

    fresh = await AnalysisReport.get(report.id)
    result = fresh.dict(by_alias=True)
    result["status"] = "complete" if next_action == "generate_report" else "manual_review_required"
    return result

async def resume_analysis_session(
    session_id: str,
    user_id: str,
    answers: dict,
    background_tasks: Optional[BackgroundTasks] = None,
    on_stage: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    log_section("Clarification Session")
    logger.info(f"[Clarification] Resuming session {session_id} with {len(answers)} user answers")
    session = await AnalysisSession.get(ObjectId(session_id))
    if not session or session.user_id != user_id:
        raise ValueError("Session not found or unauthorized")

    if session.status not in ("waiting_for_user", "needs_clarification"):
        logger.warning(f"[Clarification] Session {session_id} has unexpected status '{session.status}'")
        raise ValueError(f"Session is not waiting for user input (current status: {session.status})")

    tracker = AnalysisCostTracker(session_id=str(session.id))
    if session.cost_steps:
        tracker.load_steps(session.cost_steps)
    set_current_cost_tracker(tracker)

    logger.debug(f"[Clarification] Storing {len(answers)} answers for session {session_id}")
    for q_id, answer_val in answers.items():
        session.answers.append(ClarificationAnswer(
            questionId=q_id,
            answer=answer_val
        ))
    
    # Check if policy start date conflict was answered
    date_ans = answers.get("policy_start_date_conflict")
    if date_ans:
        chosen_parsed = _date_from_answer(date_ans)
        if chosen_parsed:
            chosen_iso_date = chosen_parsed.strftime("%Y-%m-%d")
            if not session.policy_json:
                session.policy_json = {}
            session.policy_json["policyStartDate"] = chosen_iso_date
            logger.info(f"[Clarification] Resolved policy start date from user answer: {chosen_iso_date}")
            # A new policy was first taken on its start date, so waiting periods count from it too.
            is_new_policy = session.policy_json.get("isRenewal") is False
            if is_new_policy:
                session.policy_json["declaredInceptionDate"] = chosen_iso_date
                session.policy_json["inceptionDate"] = chosen_iso_date
                session.policy_json["waitingPeriodStartDate"] = chosen_iso_date
                session.policy_json["waitingPeriodBasis"] = (
                    f"Based on your answer, this is a new policy first taken on {chosen_parsed:%d-%b-%Y}"
                )

            # Persist chosen start date in Policy MongoDB record
            if session.policy_id:
                try:
                    if len(str(session.policy_id)) == 24:
                        pol_record = await Policy.get(ObjectId(session.policy_id))
                        if pol_record:
                            pol_record.policy_start_date = chosen_parsed
                            if is_new_policy:
                                pol_record.inception_date = chosen_parsed
                            if pol_record.extracted_policy_json:
                                pol_record.extracted_policy_json["policyStartDate"] = chosen_iso_date
                            await pol_record.save()
                            logger.debug(f"[Clarification] Saved resolved policy start date {chosen_parsed} to Policy {pol_record.id}")
                except Exception as pe:
                    logger.error(f"[Clarification] Error updating policy start date in DB: {pe}")

    # Check if the inception date conflict was answered
    inception_ans = answers.get("policy_inception_date_conflict")
    if inception_ans:
        chosen = _date_from_answer(inception_ans)
        if chosen:
            if not session.policy_json:
                session.policy_json = {}
            session.policy_json["waitingPeriodStartDate"] = chosen.strftime("%Y-%m-%d")
            session.policy_json["waitingPeriodBasis"] = (
                f"Based on your answer, cover has been continuous since {chosen:%d-%b-%Y}"
            )
            if session.policy_id and len(str(session.policy_id)) == 24:
                try:
                    pol_record = await Policy.get(ObjectId(session.policy_id))
                    if pol_record:
                        pol_record.inception_date = chosen
                        await pol_record.save()
                except Exception as pe:
                    logger.error(f"[Clarification] Error saving inception date: {pe}")

    # Check if treatment date was answered (a picked date such as "14-Oct-2026", or today/yesterday)
    treatment_date_ans = answers.get("prescription_treatment_date")
    if treatment_date_ans:
        chosen_parsed = _date_from_answer(treatment_date_ans)
        if chosen_parsed:
            if not session.prescription_json:
                session.prescription_json = {}
            session.prescription_json["visitDate"] = chosen_parsed.strftime("%Y-%m-%d")
            session.prescription_json["consultationDate"] = chosen_parsed.strftime("%Y-%m-%d")

    # Check if hospitalization status was answered
    hosp_ans = str(answers.get("hospitalization_status", "") or answers.get("admission_type", "")).lower()
    if hosp_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        # Order matters: "Not decided yet" contains "no", and "Outpatient" contains "patient".
        if any(k in hosp_ans for k in ("not decided", "not sure", "don't know", "dont know")):
            # Purpose of the check is "if I go to hospital": evaluate as an admission, and say so.
            session.prescription_json["hospitalizationRequired"] = True
            session.prescription_json["admissionAssumed"] = True
        elif any(k in hosp_ans for k in ("opd", "outpatient", "out-patient", "clinic", "not required", "not admitted")) \
                or hosp_ans.strip() == "no":
            session.prescription_json["hospitalizationRequired"] = False
        elif any(k in hosp_ans for k in ("yes", "admitted", "admission", "planned", "inpatient", "in-patient", "daycare", "day care")):
            session.prescription_json["hospitalizationRequired"] = True

    # Check if pre-existing status was answered
    ped_ans = str(answers.get("pre_existing_status", "")).lower()
    if ped_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        if "yes" in ped_ans or "pre-existing" in ped_ans:
            session.prescription_json["isPreExisting"] = True
        elif "no" in ped_ans or "first time" in ped_ans or "new" in ped_ans:
            session.prescription_json["isPreExisting"] = False

    # Check if symptom onset / duration / past medical history was answered
    onset_ans = str(answers.get("symptom_onset_duration", "")).lower()
    pmh_ans = str(answers.get("past_medical_history", "")).lower()
    onset_date = _date_from_answer(answers.get("symptom_onset_duration")) if "today" not in onset_ans else None
    if onset_date:
        # The user entered the actual onset date: pre-existing if it is before the policy started.
        if not session.prescription_json:
            session.prescription_json = {}
        session.prescription_json["diagnosisDate"] = onset_date.strftime("%Y-%m-%d")
        pol_start_dt, _ = _parse_and_format_date((session.policy_json or {}).get("policyStartDate"))
        if pol_start_dt:
            session.prescription_json["isPreExisting"] = onset_date < pol_start_dt
    elif "pre-existing" in onset_ans or "pre-existing" in pmh_ans or "prior to policy" in onset_ans or "yes, previously" in pmh_ans or "years ago" in onset_ans or "months before" in onset_ans or "chronic" in onset_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        session.prescription_json["isPreExisting"] = True
        import re
        from datetime import timedelta
        match = re.search(r"(\d{2}[-/]\d{2}[-/]\d{4}|\d{4}[-/]\d{2}[-/]\d{2})", str(onset_ans))
        if match:
            parsed_onset, _ = _parse_and_format_date(match.group(1))
            if parsed_onset:
                session.prescription_json["diagnosisDate"] = parsed_onset.strftime("%Y-%m-%d")
        else:
            # Approximate diagnosis date relative to policy start date
            pol_start_dt = None
            if session.policy_json and session.policy_json.get("policyStartDate"):
                pol_start_dt, _ = _parse_and_format_date(session.policy_json.get("policyStartDate"))
            base_date = pol_start_dt or datetime.now()

            if "more than 2 years" in onset_ans or "> 2 years" in onset_ans or "long-standing" in onset_ans:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=750)).strftime("%Y-%m-%d")
            elif "1 to 2 years" in onset_ans or "1 year" in onset_ans:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=400)).strftime("%Y-%m-%d")
            elif "1 to 6 months" in onset_ans or "recent" in onset_ans:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=90)).strftime("%Y-%m-%d")
            else:
                session.prescription_json["diagnosisDate"] = (base_date - timedelta(days=180)).strftime("%Y-%m-%d")
    elif "today" in onset_ans or "first time" in pmh_ans or "new illness" in onset_ans:
        if not session.prescription_json:
            session.prescription_json = {}
        session.prescription_json["isPreExisting"] = False

    policy_json = session.policy_json if hasattr(session, "policy_json") and session.policy_json else {}
    prescription_json = session.prescription_json or {}
    await _report_stage(on_stage, "rule_engine")
    br_results = enforce_business_rules(policy_json, prescription_json, policy_text=session.policy_text)
    session.business_rules = br_results
    session.status = "reanalyzing"
    await session.save()

    logger.info(f"[Claim Analysis] Re-running AI analysis for session {session_id}")
    return await _run_llm_analysis(
        session, policy_json, prescription_json, br_results, background_tasks, on_stage=on_stage
    )
