"""
Policy Extraction Service — migrated from PolicyExtractionService.js.
AI-powered policy JSON extraction with chunking.
"""

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry
from app.services.llm.prompts import POLICY_EXTRACTION_PROMPT, POLICY_FIELDS, REQUIRED_POLICY_FIELDS
from app.utils.document_splitter import split_text_intelligently, merge_extracted_json
from app.utils.extraction_validator import validate_extraction


async def extract_policy_details(policy_text: str) -> dict:
    """
    Extracts structured JSON data from raw policy text.
    Uses chunking if text exceeds the context window.
    """
    logger.info("[Policy] Extracting structured policy terms...")
    
    if not policy_text or len(policy_text.strip()) < 50:
        return {
            "isValid": False,
            "confidence": 0,
            "extractedJson": None,
            "errors": ["Policy text is too short or empty."],
            "warnings": []
        }
        
    import time
    start_time = time.time()
    
    # Restored to use the intelligent default of 24,000 characters
    chunks = split_text_intelligently(policy_text)
    logger.debug(f"[Policy] Split policy text into {len(chunks)} chunk(s).")
    
    extracted_jsons = []
    
    total_prompt_prep = 0.0
    total_llm_processing = 0.0
    total_json_parsing = 0.0
    
    import asyncio
    
    async def extract_chunk(i, chunk):
        if len(chunks) > 1:
            logger.info(f"[Policy] Processing chunk {i+1}/{len(chunks)}...")
        try:
            result = await extract_json_with_retry(
                system_prompt=POLICY_EXTRACTION_PROMPT,
                user_content=chunk,
                max_tokens=4000,
                operation_name="Policy JSON Extraction" if len(chunks) == 1 else f"Policy JSON Extraction (Chunk {i+1}/{len(chunks)})",
            )
            return i, result
        except Exception as e:
            logger.error(f"[Policy] Error extracting chunk {i+1}: {e}")
            return i, None

    tasks = [extract_chunk(i, chunk) for i, chunk in enumerate(chunks)]
    results = await asyncio.gather(*tasks)

    # Sort results to ensure original order
    results.sort(key=lambda x: x[0])
    
    for _, result in results:
        if result:
            if "timing" in result:
                total_prompt_prep += result["timing"].get("promptPreparationSec", 0)
                total_llm_processing += result["timing"].get("llmProcessingSec", 0)
                total_json_parsing += result["timing"].get("jsonParsingSec", 0)
                
            if result.get("extractedJson"):
                extracted_jsons.append(result["extractedJson"])
            
    if not extracted_jsons:
        return {
            "isValid": False,
            "confidence": 0,
            "extractedJson": None,
            "errors": ["Failed to extract any JSON from the policy document."],
            "warnings": []
        }
        
    final_json = merge_extracted_json(extracted_jsons)
    
    total_processing = time.time() - start_time
    logger.debug(f"[Policy] Extraction timings: prompt={total_prompt_prep:.2f}s, llm={total_llm_processing:.2f}s, parse={total_json_parsing:.2f}s, total={total_processing:.2f}s")
    
    validation_result = validate_extraction(
        extracted_json=final_json,
        required_fields=REQUIRED_POLICY_FIELDS,
        all_expected_fields=POLICY_FIELDS
    )
    
    logger.info(f"[Policy] Extraction completed (Valid: {validation_result['isValid']}, Confidence: {validation_result['confidence']}%)")
    
    return {
        "isValid": validation_result["isValid"],
        "confidence": validation_result["confidence"],
        "extractedJson": validation_result["cleanedJson"],
        "errors": validation_result["errors"],
        "warnings": validation_result["warnings"]
    }
