"""
Analysis Orchestrator — migrated from analysisOrchestrator.js.
Coordinates the entire 11-stage AI processing pipeline asynchronously.
"""

import time
import asyncio
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId as ObjectId

from app.core.logging import logger
from app.models.analysis_report import AnalysisReport
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.models.stored_file import StoredFile

from app.services.extraction.document_extraction import extract_text_from_document
from app.services.extraction.text_cleaning import clean_text
from app.services.extraction.policy_extraction import extract_policy_details
from app.services.extraction.prescription_extraction import extract_prescription_details
from app.services.extraction.json_validation import validate_extracted_json
from app.services.coverage.business_rules import enforce_business_rules
from app.services.coverage.coverage_analysis import analyze_coverage
from app.services.coverage.report_generator import generate_report
from app.services.extraction.metadata_extraction import extract_policy_metadata, extract_prescription_metadata
from app.services.storage.file_upload_service import FileUploadService


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
    if not stored_file or stored_file.user_id != user_id:
         raise FileNotFoundError(f"File metadata not found for ID {file_id}")
         
    chunks = []
    async for chunk in await FileUploadService.get_file_stream(stored_file.storage_key):
        chunks.append(chunk)
        
    return b"".join(chunks), stored_file.mime_type


from fastapi import BackgroundTasks

async def _background_post_processing(
    user_id: str,
    report_id: str,
    policy_doc_id: Optional[str],
    policy_file_id: Optional[str],
    prescription_id: str
):
    """
    Executes Metadata Extraction and RAG Indexing in the background.
    """
    try:
        report = await AnalysisReport.get(ObjectId(report_id))
        if not report:
            return

        # ─── Stage 10: Extract & Persist Metadata in Policies & Prescriptions Collections ───
        logger.info("[Orchestrator] Stage 10: Extracting & Storing Metadata in Policies and Prescriptions Collections")
        policy_meta = {}
        rx_meta = {}
        try:
            policy_meta, rx_meta = await asyncio.gather(
                extract_policy_metadata(report.policy_text or ""),
                extract_prescription_metadata(report.prescription_text or ""),
                return_exceptions=True
            )
            if isinstance(policy_meta, Exception):
                logger.error(f"[Orchestrator] Policy metadata extraction error: {policy_meta}")
                policy_meta = {}
            if isinstance(rx_meta, Exception):
                logger.error(f"[Orchestrator] Prescription metadata extraction error: {rx_meta}")
                rx_meta = {}

            report.policy_metadata = policy_meta
            report.prescription_metadata = rx_meta

            # 1. Update / Insert in `policies` collection
            target_policy_doc = None
            if policy_doc_id:
                try:
                    target_policy_doc = await Policy.get(ObjectId(policy_doc_id))
                except Exception:
                    pass

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
                target_policy_doc.metadata = policy_meta
                target_policy_doc.extracted_policy_text = report.policy_text
                target_policy_doc.extracted_policy_json = report.policy_json
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
                
                p_start = _safe_parse_datetime(policy_meta.get("policy_start_date"))
                if p_start:
                    target_policy_doc.policy_start_date = p_start
                p_end = _safe_parse_datetime(policy_meta.get("policy_expiry_date"))
                if p_end:
                    target_policy_doc.policy_end_date = p_end

                target_policy_doc.processing_status = "completed"
                await target_policy_doc.save()
                report.policy_id = str(target_policy_doc.id)
            elif policy_file_id:
                new_policy = Policy(
                    user_id=user_id,
                    insurance_company=policy_meta.get("provider_name") or (report.policy_json.get("insuranceCompany") if report.policy_json else None),
                    policy_number=str(policy_meta.get("policy_number")) if policy_meta.get("policy_number") else (str(report.policy_json.get("policyNumber")) if report.policy_json and report.policy_json.get("policyNumber") else None),
                    policy_name=str(policy_meta.get("plan_name")) if policy_meta.get("plan_name") else None,
                    policy_holder_name=str(p_holder) if p_holder else None,
                    policy_type=str(policy_meta.get("policy_type")) if policy_meta.get("policy_type") else (str(report.policy_json.get("policyType")) if report.policy_json and report.policy_json.get("policyType") else None),
                    coverage_amount=_safe_parse_float(policy_meta.get("sum_insured") or policy_meta.get("available_sum_insured")),
                    policy_start_date=_safe_parse_datetime(policy_meta.get("policy_start_date")),
                    policy_end_date=_safe_parse_datetime(policy_meta.get("policy_expiry_date")),
                    grid_fs_file_id=policy_file_id,
                    extracted_policy_text=report.policy_text,
                    extracted_policy_json=report.policy_json,
                    metadata=policy_meta,
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

            if not target_rx_doc:
                target_rx_doc = await Prescription.find_one(
                    Prescription.grid_fs_file_id == prescription_id,
                    Prescription.user_id == user_id
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
                target_rx_doc.metadata = rx_meta
                target_rx_doc.extracted_prescription_text = report.prescription_text
                target_rx_doc.extracted_prescription_json = report.prescription_json
                if rx_hospital:
                    target_rx_doc.hospital_name = str(rx_hospital)
                if rx_doctor:
                    target_rx_doc.doctor_name = str(rx_doctor)
                if rx_patient:
                    target_rx_doc.patient_name = str(rx_patient)
                if rx_number:
                    target_rx_doc.prescription_number = str(rx_number)
                if diag_str:
                    target_rx_doc.diagnosis = str(diag_str)
                if rx_visit_date:
                    target_rx_doc.visit_date = rx_visit_date
                target_rx_doc.processing_status = "completed"
                await target_rx_doc.save()
                report.prescription_id = str(target_rx_doc.id)
            else:
                new_rx = Prescription(
                    user_id=user_id,
                    hospital_name=str(rx_hospital) if rx_hospital else None,
                    doctor_name=str(rx_doctor) if rx_doctor else None,
                    patient_name=str(rx_patient) if rx_patient else None,
                    prescription_number=str(rx_number) if rx_number else None,
                    visit_date=rx_visit_date,
                    diagnosis=str(diag_str) if diag_str else None,
                    grid_fs_file_id=prescription_id,
                    extracted_prescription_text=report.prescription_text,
                    extracted_prescription_json=report.prescription_json,
                    metadata=rx_meta,
                    processing_status="completed"
                )
                await new_rx.insert()
                report.prescription_id = str(new_rx.id)

            await report.save()
        except Exception as meta_err:
            logger.error(f"[Orchestrator Background] Error during metadata extraction/persistence: {meta_err}", exc_info=True)

        # Stage 10.5: Index documents for RAG
        logger.info("[Orchestrator] Stage 10.5: Indexing documents for RAG")
        from app.services.rag.indexing import process_and_index_document
        
        try:
            if report.policy_id and report.policy_text:
                await process_and_index_document(
                    user_id=user_id,
                    document_id=report.policy_id,
                    document_type="policy",
                    text=report.policy_text
                )
            
            if report.prescription_id and report.prescription_text:
                await process_and_index_document(
                    user_id=user_id,
                    document_id=report.prescription_id,
                    document_type="prescription",
                    text=report.prescription_text
                )
        except Exception as rag_err:
            logger.error(f"[Orchestrator Background] Error during RAG indexing: {rag_err}", exc_info=True)
            
        logger.info("[Orchestrator Background] Post-processing complete.")

    except Exception as e:
        logger.error(f"[Orchestrator Background] Fatal error: {e}", exc_info=True)


async def run_analysis_pipeline(
    user_id: str,
    prescription_id: str,
    policy_file_id: Optional[str] = None,
    policy_doc_id: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None
) -> dict:
    """
    Executes the 11-stage analysis pipeline.
    """
    start_time = time.time()
    logger.info(f"[Orchestrator] Starting pipeline for User: {user_id}")
    
    report = AnalysisReport(
        user_id=user_id,
        policy_id=policy_doc_id or policy_file_id,
        prescription_id=prescription_id,
        status="extracting",
        analysis_version="2.0.0"
    )
    await report.insert()
    
    try:
        # Stage 1: Load Metadata
        logger.info("[Orchestrator] Stage 1: Validating file IDs")
        
        # Stage 2 & 3: Extract & Clean Text
        logger.info("[Orchestrator] Stage 2 & 3: Extracting and Cleaning Text")
        
        async def process_document(doc_id):
            bytes_data, mime = await _fetch_file_bytes_by_gridfs_id(doc_id, user_id)
            raw_text = await extract_text_from_document(bytes_data, mime)
            return clean_text(raw_text)

        policy_text = ""
        existing_policy_json = None
        
        if policy_doc_id:
            logger.info(f"[Orchestrator] Reusing existing policy: {policy_doc_id}")
            policy_doc = await Policy.get(ObjectId(policy_doc_id))
            if not policy_doc or policy_doc.user_id != user_id:
                raise ValueError("Invalid Policy ID or unauthorized")
                
            if policy_doc.extracted_policy_text:
                policy_text = policy_doc.extracted_policy_text
                existing_policy_json = policy_doc.extracted_policy_json or {}
                rx_text = await process_document(prescription_id)
            else:
                logger.info("[Orchestrator] Policy has no extracted text, extracting now")
                policy_text, rx_text = await asyncio.gather(
                    process_document(policy_doc.grid_fs_file_id),
                    process_document(prescription_id)
                )
        else:
            logger.info("[Orchestrator] Extracting new policy and prescription from files")
            policy_text, rx_text = await asyncio.gather(
                process_document(policy_file_id),
                process_document(prescription_id)
            )
        
        report.policy_text = policy_text
        report.prescription_text = rx_text
        await report.save()
        
        # Stage 4 & 5: JSON Extraction
        logger.info("[Orchestrator] Stage 4 & 5: JSON Extraction")
        
        if existing_policy_json:
            policy_data = {"extractedJson": existing_policy_json}
            rx_data = await extract_prescription_details(rx_text)
        else:
            policy_data, rx_data = await asyncio.gather(
                extract_policy_details(policy_text),
                extract_prescription_details(rx_text)
            )
        
        # Stage 6: JSON Validation
        logger.info("[Orchestrator] Stage 6: JSON Validation")
        validation = validate_extracted_json(
            policy_data.get("extractedJson"),
            rx_data.get("extractedJson")
        )
        
        report.policy_json = validation["validatedPolicyJson"]
        report.prescription_json = validation["validatedPrescriptionJson"]
        report.status = "analyzing"
        await report.save()
        
        # Stage 7: Business Rules
        import json
        logger.info("[Orchestrator] Stage 7: Business Rules")
        br_results = enforce_business_rules(
            report.policy_json,
            report.prescription_json
        )
        report.business_rules = br_results
        
        # Stage 8: Deep AI Coverage Analysis
        logger.info("[Orchestrator] Stage 8: AI Coverage Analysis")
        coverage_analysis = await analyze_coverage(
            policy_text=report.policy_text or "",
            prescription_text=report.prescription_text or "",
            business_rule_results=br_results,
            prescription_json=report.prescription_json or {}
        )
        report.coverage_analysis = coverage_analysis
        
        # Stage 9: Generate Coverage Report
        logger.info("[Orchestrator] Stage 9: Generating Coverage Report")
        processing_time = int((time.time() - start_time) * 1000)
        
        final_report = generate_report(
            policy_json=report.policy_json,
            prescription_json=report.prescription_json,
            business_rule_results=br_results,
            coverage_analysis=coverage_analysis,
            processing_time_ms=processing_time
        )
        
        # Add Stage 10 and 10.5 to background tasks
        if background_tasks:
            background_tasks.add_task(
                _background_post_processing,
                user_id=user_id,
                report_id=str(report.id),
                policy_doc_id=policy_doc_id,
                policy_file_id=policy_file_id,
                prescription_id=prescription_id
            )
        else:
            # If no background_tasks is provided, we run it synchronously (for safety)
            await _background_post_processing(
                user_id=user_id,
                report_id=str(report.id),
                policy_doc_id=policy_doc_id,
                policy_file_id=policy_file_id,
                prescription_id=prescription_id
            )
            
        # Stage 11: Finalize and Save AnalysisReport
        logger.info("[Orchestrator] Stage 11: Finalizing and Saving Report")
        report.status = "completed"
        report.document_validity = final_report.get("documentValidity")
        report.overall_status = final_report.get("overallStatus")
        report.dominance_score = final_report.get("dominanceScore")
        report.coverage_breakdown = final_report.get("coverageBreakdown")
        report.summary = final_report.get("summary")
        report.summary_text = final_report.get("summaryText")
        report.comparison = final_report.get("comparison")
        report.processing_time_ms = processing_time
        
        await report.save()
        logger.info(f"[Orchestrator] Pipeline completed successfully in {processing_time}ms.")
        
        # Fetch fresh complete document to return as dict
        fresh = await AnalysisReport.get(report.id)
        return fresh.dict(by_alias=True)
        
    except Exception as e:
        logger.error(f"[Orchestrator] Pipeline failed: {e}")
        report.status = "failed"
        report.error_message = str(e)
        report.processing_time_ms = int((time.time() - start_time) * 1000)
        await report.save()
        raise e
