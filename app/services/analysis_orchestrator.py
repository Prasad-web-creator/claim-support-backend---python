"""
Analysis Orchestrator — migrated from analysisOrchestrator.js.
Coordinates the entire 11-stage AI processing pipeline asynchronously.
"""

import time
import asyncio
from typing import Optional
from bson import ObjectId

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
from app.services.storage.file_upload_service import FileUploadService


async def _fetch_file_bytes_by_gridfs_id(file_id: str, user_id: str) -> tuple[bytes, str]:
    from bson import ObjectId
    from app.models.stored_file import StoredFile
    from app.services.storage.file_upload_service import FileUploadService
    
    stored_file = await StoredFile.get(ObjectId(file_id))
    if not stored_file or stored_file.user_id != user_id:
         raise FileNotFoundError(f"File metadata not found for ID {file_id}")
         
    chunks = []
    async for chunk in await FileUploadService.get_file_stream(stored_file.storage_key):
        chunks.append(chunk)
        
    return b"".join(chunks), stored_file.mime_type


async def run_analysis_pipeline(
    user_id: str,
    prescription_id: str,
    policy_file_id: Optional[str] = None,
    policy_doc_id: Optional[str] = None
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
        
        print("-------------------------------------------------------------------------------------------------------------------------")
        print("Policy Text : \n",policy_text[:500], "... (truncated)")
        print("-------------------------------------------------------------------------------------------------------------------------")
        print("Prescription Text : \n",rx_text)
        print("-------------------------------------------------------------------------------------------------------------------------")
        
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
        
        # Save new policy to DB if it was uploaded OR update the un-extracted one
        if policy_doc_id and not existing_policy_json:
            logger.info("[Orchestrator] Updating un-extracted policy in database")
            policy_doc.extracted_policy_text = policy_text
            policy_doc.extracted_policy_json = report.policy_json
            policy_doc.insurance_company = report.policy_json.get("insuranceCompany") or policy_doc.insurance_company
            policy_doc.policy_number = report.policy_json.get("policyNumber") or policy_doc.policy_number
            policy_doc.policy_type = report.policy_json.get("policyType") or policy_doc.policy_type
            policy_doc.processing_status = "completed"
            await policy_doc.save()
            report.policy_id = str(policy_doc.id)
            await report.save()
        elif policy_file_id and not policy_doc_id:
            logger.info("[Orchestrator] Saving new policy to database")
            new_policy = Policy(
                user_id=user_id,
                insurance_company=report.policy_json.get("insuranceCompany"),
                policy_number=report.policy_json.get("policyNumber"),
                policy_type=report.policy_json.get("policyType"),
                grid_fs_file_id=policy_file_id,
                extracted_policy_text=policy_text,
                extracted_policy_json=report.policy_json,
                processing_status="completed"
            )
            await new_policy.insert()
            report.policy_id = str(new_policy.id)
            await report.save()
        
        # Stage 7: Business Rules
        import json
        logger.info("[Orchestrator] Stage 7: Business Rules")
        logger.debug(f"[Orchestrator] DEBUG - Policy Data Before BR: {json.dumps(report.policy_json, default=str)}")
        logger.debug(f"[Orchestrator] DEBUG - Prescription Data Before BR: {json.dumps(report.prescription_json, default=str)}")
        
        # Debug log specifically for string fields susceptible to NoneType errors
        string_fields_policy = ["coveredDiseases", "excludedDiseases", "hospitalization"]
        for f in string_fields_policy:
            val = report.policy_json.get(f)
            logger.debug(f"[Orchestrator] DEBUG - Policy Field '{f}': Type {type(val).__name__} = {val}")
            
        string_fields_rx = ["diagnosis"]
        for f in string_fields_rx:
            val = report.prescription_json.get(f)
            logger.debug(f"[Orchestrator] DEBUG - Prescription Field '{f}': Type {type(val).__name__} = {val}")
        
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
        
        # Stage 9: Generate Report
        logger.info("[Orchestrator] Stage 9: Generating Report")
        processing_time = int((time.time() - start_time) * 1000)
        
        final_report = generate_report(
            policy_json=report.policy_json,
            prescription_json=report.prescription_json,
            business_rule_results=br_results,
            coverage_analysis=coverage_analysis,
            processing_time_ms=processing_time
        )
        
        # Stage 10 & 11: Save and Return
        logger.info("[Orchestrator] Stage 10 & 11: Finalizing and Saving")
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
