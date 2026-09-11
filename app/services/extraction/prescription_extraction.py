"""
Prescription Extraction Service — migrated from PrescriptionExtractionService.js.
AI-powered prescription JSON extraction with chunking.
"""

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry
from app.services.llm.prompts import PRESCRIPTION_EXTRACTION_PROMPT, PRESCRIPTION_FIELDS, REQUIRED_PRESCRIPTION_FIELDS
from app.services.llm.schemas import PrescriptionSchema
from app.utils.document_splitter import split_text_intelligently, merge_extracted_json
from app.utils.extraction_validator import validate_extraction


async def extract_prescription_details(prescription_text: str) -> dict:
    """
    Extracts structured JSON data from raw prescription text.
    Uses chunking if text exceeds the context window.
    """
    logger.info("[Prescription] Extracting structured medical details...")
    
    if not prescription_text or not prescription_text.strip():
        return {
            "isValid": False,
            "confidence": 0,
            "extractedJson": None,
            "errors": ["Prescription text is empty."],
            "warnings": []
        }
        
    chunks = split_text_intelligently(prescription_text, 15000)
    logger.debug(f"[Prescription] Split prescription text into {len(chunks)} chunk(s).")
    
    extracted_jsons = []
    
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            logger.info(f"[Prescription] Processing chunk {i+1}/{len(chunks)}...")
        try:
            result = await extract_json_with_retry(
                system_prompt=PRESCRIPTION_EXTRACTION_PROMPT,
                user_content=chunk,
                max_tokens=1500,
                response_schema=PrescriptionSchema,
                operation_name="Prescription JSON Extraction" if len(chunks) == 1 else f"Prescription JSON Extraction (Chunk {i+1}/{len(chunks)})",
            )
            if result.get("extractedJson"):
                extracted_jsons.append(result["extractedJson"])
        except Exception as e:
            logger.error(f"[Prescription] Error extracting chunk {i+1}: {e}")
            
    if not extracted_jsons:
        return {
            "isValid": False,
            "confidence": 0,
            "extractedJson": None,
            "errors": ["Failed to extract any JSON from the prescription document."],
            "warnings": []
        }
        
    final_json = merge_extracted_json(extracted_jsons)
    
    
    validation_result = validate_extraction(
        extracted_json=final_json,
        required_fields=REQUIRED_PRESCRIPTION_FIELDS,
        all_expected_fields=PRESCRIPTION_FIELDS
    )
    
    logger.info(f"[Prescription] Extraction completed (Valid: {validation_result['isValid']}, Confidence: {validation_result['confidence']}%)")
    
    return {
        "isValid": validation_result["isValid"],
        "confidence": validation_result["confidence"],
        "extractedJson": validation_result["cleanedJson"],
        "errors": validation_result["errors"],
        "warnings": validation_result["warnings"]
    }
