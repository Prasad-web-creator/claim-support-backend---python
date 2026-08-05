"""
Metadata Extraction Service
Extracts structured document metadata using METADATA_EXTRACTION_PROMPT.
"""

from typing import Optional
from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry
from app.services.llm.prompts import METADATA_EXTRACTION_PROMPT
from app.utils.document_splitter import split_text_intelligently, merge_extracted_json


POLICY_METADATA_TEMPLATE = {
    "document_type": "policy",
    "provider_name": None,
    "policy_number": None,
    "policy_type": None,
    "plan_name": None,
    "policy_holder_name": None,
    "insured_person_name": None,
    "member_id": None,
    "certificate_number": None,
    "customer_id": None,
    "sum_insured": None,
    "available_sum_insured": None,
    "cumulative_bonus": None,
    "policy_start_date": None,
    "policy_expiry_date": None,
    "policy_issue_date": None,
    "renewal_date": None,
    "waiting_period": None,
    "pre_existing_waiting_period": None,
    "room_rent_limit": None,
    "icu_limit": None,
    "co_payment_percentage": None,
    "deductible_amount": None,
    "network_type": None,
    "cashless_available": None,
    "coverage_type": None,
    "policy_status": None,
    "nominee_name": None,
    "agent_name": None,
    "agent_code": None,
    "policy_branch": None,
    "contact_number": None,
    "email": None,
    "address": None,
}

PRESCRIPTION_METADATA_TEMPLATE = {
    "document_type": "prescription",
    "patient_name": None,
    "patient_age": None,
    "patient_gender": None,
    "patient_id": None,
    "hospital_name": None,
    "clinic_name": None,
    "doctor_name": None,
    "doctor_registration_number": None,
    "department": None,
    "specialization": None,
    "hospital_visit_date": None,
    "consultation_date": None,
    "admission_date": None,
    "discharge_date": None,
    "follow_up_date": None,
    "diagnosis": [],
    "icd_codes": [],
    "symptoms": [],
    "medical_history": [],
    "allergies": [],
    "vital_signs": {
        "blood_pressure": None,
        "pulse": None,
        "temperature": None,
        "oxygen_saturation": None,
        "weight": None,
        "height": None,
        "bmi": None,
    },
    "prescribed_medicines": [],
    "recommended_tests": [],
    "recommended_procedures": [],
    "surgeries": [],
    "hospitalization_required": None,
    "emergency_case": None,
    "estimated_cost": None,
    "insurance_reference_number": None,
}


def _sanitize_policy_metadata(raw_data: Optional[dict]) -> dict:
    result = dict(POLICY_METADATA_TEMPLATE)
    if not isinstance(raw_data, dict):
        return result
    for key in POLICY_METADATA_TEMPLATE:
        if key in raw_data and raw_data[key] is not None:
            result[key] = raw_data[key]
    result["document_type"] = "policy"
    return result


def _sanitize_prescription_metadata(raw_data: Optional[dict]) -> dict:
    result = dict(PRESCRIPTION_METADATA_TEMPLATE)
    if not isinstance(raw_data, dict):
        return result
    for key in PRESCRIPTION_METADATA_TEMPLATE:
        if key == "vital_signs":
            vitals = raw_data.get("vital_signs")
            if isinstance(vitals, dict):
                merged_vitals = dict(PRESCRIPTION_METADATA_TEMPLATE["vital_signs"])
                for v_key in merged_vitals:
                    if v_key in vitals and vitals[v_key] is not None:
                        merged_vitals[v_key] = vitals[v_key]
                result["vital_signs"] = merged_vitals
            else:
                result["vital_signs"] = dict(PRESCRIPTION_METADATA_TEMPLATE["vital_signs"])
        elif key in raw_data and raw_data[key] is not None:
            result[key] = raw_data[key]

    # Ensure list fields are arrays
    for list_key in [
        "diagnosis", "icd_codes", "symptoms", "medical_history",
        "allergies", "prescribed_medicines", "recommended_tests",
        "recommended_procedures", "surgeries"
    ]:
        val = result.get(list_key)
        if isinstance(val, str):
            result[list_key] = [val] if val.strip() else []
        elif not isinstance(val, list):
            result[list_key] = []

    result["document_type"] = "prescription"
    return result


async def extract_policy_metadata(policy_text: str) -> dict:
    """
    Extracts Section A Policy metadata from policy text.
    """
    logger.info("[MetadataExtraction] Starting policy metadata extraction...")
    if not policy_text or len(policy_text.strip()) < 30:
        return dict(POLICY_METADATA_TEMPLATE)

    try:
        chunks = split_text_intelligently(policy_text)
        extracted_jsons = []
        for i, chunk in enumerate(chunks):
            result = await extract_json_with_retry(
                system_prompt=METADATA_EXTRACTION_PROMPT,
                user_content=f"Document Type: Policy\n\nDocument Text:\n{chunk}",
                max_tokens=4000
            )
            if result.get("extractedJson"):
                extracted_jsons.append(result["extractedJson"])

        if extracted_jsons:
            merged = merge_extracted_json(extracted_jsons) if len(extracted_jsons) > 1 else extracted_jsons[0]
            return _sanitize_policy_metadata(merged)
    except Exception as e:
        logger.error(f"[MetadataExtraction] Policy metadata extraction failed: {e}")

    return dict(POLICY_METADATA_TEMPLATE)


async def extract_prescription_metadata(rx_text: str) -> dict:
    """
    Extracts Section B Prescription / Medical metadata from prescription text.
    """
    logger.info("[MetadataExtraction] Starting prescription metadata extraction...")
    if not rx_text or len(rx_text.strip()) < 30:
        return dict(PRESCRIPTION_METADATA_TEMPLATE)

    try:
        chunks = split_text_intelligently(rx_text)
        extracted_jsons = []
        for i, chunk in enumerate(chunks):
            result = await extract_json_with_retry(
                system_prompt=METADATA_EXTRACTION_PROMPT,
                user_content=f"Document Type: Prescription\n\nDocument Text:\n{chunk}",
                max_tokens=4000
            )
            if result.get("extractedJson"):
                extracted_jsons.append(result["extractedJson"])

        if extracted_jsons:
            merged = merge_extracted_json(extracted_jsons) if len(extracted_jsons) > 1 else extracted_jsons[0]
            return _sanitize_prescription_metadata(merged)
    except Exception as e:
        logger.error(f"[MetadataExtraction] Prescription metadata extraction failed: {e}")

    return dict(PRESCRIPTION_METADATA_TEMPLATE)
