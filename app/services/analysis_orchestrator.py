"""
Analysis Orchestrator — migrated from analysisOrchestrator.js.
Coordinates the entire 11-stage AI processing pipeline asynchronously.
"""

import asyncio
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId as ObjectId

from app.core.logging import logger, log_section
from app.core.exceptions import OCRError
from app.models.analysis_report import AnalysisReport
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.models.stored_file import StoredFile

from app.services.extraction.metadata_extraction import extract_policy_metadata, extract_prescription_metadata
from app.services.storage.file_upload_service import FileUploadService


def public_analysis_error(error: Exception) -> str:
    if isinstance(error, OCRError):
        return error.message
    if isinstance(error, FileNotFoundError):
        return "The uploaded document is no longer available. Please upload it again."
    return "Analysis could not be completed. Please try again."


def _safe_parse_datetime(d_val):
    if not d_val:
        return None
    if isinstance(d_val, datetime):
        return d_val
    try:
        from datetime import datetime as dt
        clean_d = str(d_val).strip().split("T")[0]
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%Y"):
            try:
                return dt.strptime(clean_d, fmt)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _safe_parse_float(val):
    if val is None:
        return None
    try:
        import re
        cleaned = re.sub(r"[^\d.]", "", str(val))
        return float(cleaned) if cleaned else None
    except Exception:
        return None


async def _fetch_file_bytes_by_gridfs_id(file_id: str, user_id: str) -> tuple[bytes, str]:
    from app.models.stored_file import StoredFile
    from app.services.storage.file_upload_service import FileUploadService
    
    stored_file = await StoredFile.get(ObjectId(file_id))
    if not stored_file or stored_file.user_id != user_id or stored_file.is_deleted:
        logger.warning(f"[File] Stored file missing or not owned by user: id={file_id}")
        raise FileNotFoundError(
            "The uploaded document is no longer available. Please upload it again."
        )
         
    chunks = []
    async for chunk in await FileUploadService.get_file_stream(stored_file.storage_key):
        chunks.append(chunk)
        
    return b"".join(chunks), stored_file.mime_type


async def _background_post_processing(
    user_id: str,
    report_id: str,
    policy_doc_id: Optional[str],
    policy_file_id: Optional[str],
    prescription_id: str,
    skip_prescription_metadata: bool = False
):
    """
    Updates policy and prescription metadata after analysis.

    `skip_prescription_metadata` is set for the children of a multi-policy
    session: the prescription is identical for every policy, so its metadata is
    extracted once instead of once per policy. The prescription record is still
    updated, using the already-extracted prescription JSON.
    """
    try:
        report = await AnalysisReport.get(ObjectId(report_id))
        if not report or report.user_id != user_id:
            return

        # ─── Stage 10: Extract & Persist Metadata in Policies & Prescriptions Collections ───
        log_section("Background Processing")
        logger.info("[Background] Saving extracted metadata for policy and prescription...")
        policy_meta = {}
        rx_meta = {}
        try:
            if skip_prescription_metadata:
                logger.debug("[Background] Reusing shared prescription extraction (multi-policy session).")
                policy_meta = await extract_policy_metadata(report.policy_text or "")
                rx_meta = {}
            else:
                policy_meta, rx_meta = await asyncio.gather(
                    extract_policy_metadata(report.policy_text or ""),
                    extract_prescription_metadata(report.prescription_text or ""),
                    return_exceptions=True
                )
            if isinstance(policy_meta, Exception):
                logger.error(f"[Background] Policy metadata error: {type(policy_meta).__name__}")
                policy_meta = {}
            if isinstance(rx_meta, Exception):
                logger.error(f"[Background] Prescription metadata error: {type(rx_meta).__name__}")
                rx_meta = {}

            # 1. Update / Insert in `policies` collection
            target_policy_doc = None
            if policy_doc_id:
                try:
                    target_policy_doc = await Policy.get(ObjectId(policy_doc_id))
                except Exception:
                    pass
                if target_policy_doc and target_policy_doc.user_id != user_id:
                    return

            if not target_policy_doc and policy_file_id:
                target_policy_doc = await Policy.find_one(
                    Policy.grid_fs_file_id == policy_file_id,
                    Policy.user_id == user_id
                )

            p_holder = (
                policy_meta.get("policy_holder_name")
                or policy_meta.get("insured_person_name")
                or (report.policy_json.get("policyHolderName") if report.policy_json else None)
                or (report.policy_json.get("policyHolder") if report.policy_json else None)
                or (report.policy_json.get("insuredName") if report.policy_json else None)
            )

            if target_policy_doc:
                target_policy_doc.extracted_policy_text = report.policy_text or ""
                target_policy_doc.extracted_policy_json = report.policy_json or {}
                if p_holder:
                    target_policy_doc.policy_holder_name = str(p_holder)
                if policy_meta.get("provider_name"):
                    target_policy_doc.insurance_company = policy_meta["provider_name"]
                elif report.policy_json and report.policy_json.get("insuranceCompany"):
                    target_policy_doc.insurance_company = report.policy_json["insuranceCompany"]
                if policy_meta.get("policy_number"):
                    target_policy_doc.policy_number = str(policy_meta["policy_number"])
                elif report.policy_json and report.policy_json.get("policyNumber"):
                    target_policy_doc.policy_number = str(report.policy_json["policyNumber"])
                if policy_meta.get("plan_name"):
                    target_policy_doc.policy_name = str(policy_meta["plan_name"])
                if policy_meta.get("policy_type"):
                    target_policy_doc.policy_type = str(policy_meta["policy_type"])
                elif report.policy_json and report.policy_json.get("policyType"):
                    target_policy_doc.policy_type = str(report.policy_json["policyType"])
                
                parsed_sum = _safe_parse_float(policy_meta.get("sum_insured") or policy_meta.get("available_sum_insured"))
                if parsed_sum is not None:
                    target_policy_doc.coverage_amount = parsed_sum
                
                # Only fill dates that are missing: a start date entered or confirmed by the
                # user must survive, or the next analysis cannot detect a start date conflict.
                p_start = _safe_parse_datetime(policy_meta.get("policy_start_date"))
                if p_start and not target_policy_doc.policy_start_date:
                    target_policy_doc.policy_start_date = p_start
                p_end = _safe_parse_datetime(policy_meta.get("policy_expiry_date"))
                if p_end and not target_policy_doc.policy_end_date:
                    target_policy_doc.policy_end_date = p_end

                target_policy_doc.processing_status = "completed"
                await target_policy_doc.save()
                report.policy_id = str(target_policy_doc.id)
            elif policy_file_id:
                new_policy = Policy(
                    user_id=user_id,
                    insurance_company=policy_meta.get("provider_name") or (report.policy_json.get("insuranceCompany") if report.policy_json else ""),
                    policy_number=str(policy_meta.get("policy_number")) if policy_meta.get("policy_number") else (str(report.policy_json.get("policyNumber")) if report.policy_json and report.policy_json.get("policyNumber") else ""),
                    policy_name=str(policy_meta.get("plan_name")) if policy_meta.get("plan_name") else "",
                    policy_holder_name=str(p_holder) if p_holder else "",
                    policy_type=str(policy_meta.get("policy_type")) if policy_meta.get("policy_type") else (str(report.policy_json.get("policyType")) if report.policy_json and report.policy_json.get("policyType") else ""),
                    coverage_amount=_safe_parse_float(policy_meta.get("sum_insured") or policy_meta.get("available_sum_insured")) or 0.0,
                    policy_start_date=_safe_parse_datetime(policy_meta.get("policy_start_date")),
                    policy_end_date=_safe_parse_datetime(policy_meta.get("policy_expiry_date")),
                    grid_fs_file_id=policy_file_id or "",
                    extracted_policy_text=report.policy_text or "",
                    extracted_policy_json=report.policy_json or {},
                    processing_status="completed"
                )
                await new_policy.insert()
                report.policy_id = str(new_policy.id)

            # 2. Update / Insert in `prescriptions` collection
            target_rx_doc = None
            try:
                target_rx_doc = await Prescription.get(ObjectId(prescription_id))
            except Exception:
                pass
            if target_rx_doc and target_rx_doc.user_id != user_id:
                return

            if not target_rx_doc:
                target_rx_doc = await Prescription.find_one(
                    Prescription.grid_fs_file_id == prescription_id,
                    Prescription.user_id == user_id
                )

            is_manual_rx = bool(
                (report.prescription_json and (
                    report.prescription_json.get("isManual")
                    or report.prescription_json.get("prescriptionSource") == "Self-entered Prescription"
                ))
                or (target_rx_doc and target_rx_doc.is_manual)
            )

            rx_diag = rx_meta.get("diagnosis")
            if isinstance(rx_diag, list):
                diag_str = ", ".join(str(d) for d in rx_diag if d)
            else:
                diag_str = str(rx_diag) if rx_diag else None
            if not diag_str and report.prescription_json:
                diag_str = report.prescription_json.get("diagnosis")

            rx_hospital = rx_meta.get("hospital_name") or rx_meta.get("clinic_name") or (report.prescription_json.get("hospital") if report.prescription_json else None) or (report.prescription_json.get("hospitalName") if report.prescription_json else None)
            rx_doctor = rx_meta.get("doctor_name") or (report.prescription_json.get("doctor") if report.prescription_json else None) or (report.prescription_json.get("doctorName") if report.prescription_json else None)
            rx_patient = rx_meta.get("patient_name") or rx_meta.get("patient") or (report.prescription_json.get("patientName") if report.prescription_json else None) or (report.prescription_json.get("patient") if report.prescription_json else None)
            rx_number = rx_meta.get("prescription_number") or rx_meta.get("prescription_no") or rx_meta.get("bill_number") or rx_meta.get("invoice_number") or (report.prescription_json.get("prescriptionNumber") if report.prescription_json else None)
            rx_visit_date = _safe_parse_datetime(rx_meta.get("hospital_visit_date") or rx_meta.get("consultation_date") or rx_meta.get("visit_date") or rx_meta.get("admission_date") or (report.prescription_json.get("visitDate") if report.prescription_json else None) or (report.prescription_json.get("consultationDate") if report.prescription_json else None) or (report.prescription_json.get("admissionDate") if report.prescription_json else None))

            if target_rx_doc:
                facts = {
                    "hospitalName": rx_hospital, "doctorName": rx_doctor, "patientName": rx_patient,
                    "prescriptionNumber": rx_number, "diagnosis": diag_str, "visitDate": rx_visit_date,
                }
                await Prescription.get_motor_collection().update_one(
                    {"_id": target_rx_doc.id, "userId": user_id},
                    {"$set": {
                        "extractedPrescriptionText": report.prescription_text or "",
                        "extractedPrescriptionJson": report.prescription_json or {},
                        "isManual": is_manual_rx,
                        "processingStatus": "completed",
                        "updatedAt": datetime.now(),
                        **{key: val for key, val in facts.items() if val},
                    }},
                )
                report.prescription_id = str(target_rx_doc.id)
            else:
                new_rx = Prescription(
                    user_id=user_id,
                    hospital_name=str(rx_hospital) if rx_hospital else "",
                    doctor_name=str(rx_doctor) if rx_doctor else "",
                    patient_name=str(rx_patient) if rx_patient else "",
                    prescription_number=str(rx_number) if rx_number else "",
                    visit_date=rx_visit_date or "",
                    diagnosis=str(diag_str) if diag_str else "",
                    is_manual=is_manual_rx,
                    grid_fs_file_id="" if is_manual_rx else (prescription_id or ""),
                    extracted_prescription_text=report.prescription_text or "",
                    extracted_prescription_json=report.prescription_json or {},
                    processing_status="completed"
                )
                await new_rx.insert()
                report.prescription_id = str(new_rx.id)

            await report.save()
        except Exception as meta_err:
            logger.error(f"[Background] Error during metadata extraction: {type(meta_err).__name__}")

        logger.info("[Background] Post-processing complete.")

    except Exception as e:
        logger.error(f"[Background] Fatal error: {type(e).__name__}")
