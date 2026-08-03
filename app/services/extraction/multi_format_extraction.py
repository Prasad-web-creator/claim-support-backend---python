"""
Multi-Format Extraction Service — migrated from MultiFormatExtractionService.js.
Unified pipeline for intelligent document processing based on classification.
"""

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_with_retry
from app.services.llm.prompts import EXTRACTION_PROMPTS
from app.services.extraction.document_extraction import extract_text_from_document
from app.services.extraction.document_classification import classify_document_from_text


async def process_document(buffer: bytes, mime_type: str) -> dict:
    """
    End-to-end intelligent document processing pipeline:
    1. Extract Text
    2. Classify Document Type
    3. Extract Schema-based JSON
    """
    logger.info("[MultiFormat] Starting intelligent document processing")
    
    # 1. Extract Text
    raw_text = await extract_text_from_document(buffer, mime_type)
    
    if not raw_text or len(raw_text.strip()) < 20:
        return {
            "success": False,
            "documentType": "Unknown",
            "confidence": 0,
            "extractedData": None,
            "message": "Could not extract sufficient readable text from the document."
        }
        
    # 2. Classify Document
    classification = await classify_document_from_text(raw_text)
    doc_type = classification["documentType"]
    conf = classification["confidence"]
    
    logger.info(f"[MultiFormat] Classified as {doc_type} (Confidence: {conf})")
    
    # 3. Extract JSON based on classification
    prompt = EXTRACTION_PROMPTS.get(doc_type, EXTRACTION_PROMPTS["Other"])
    
    # Take chunk of text if too large for generic extraction
    snippet = raw_text[:24000]
    
    try:
        result = await extract_json_with_retry(prompt, snippet)
        
        return {
            "success": True,
            "documentType": doc_type,
            "confidence": conf,
            "extractedData": result.get("extractedJson", {}),
            "classificationReasoning": classification.get("reasoning"),
            "rawTextAvailable": True
        }
    except Exception as e:
        logger.error(f"[MultiFormat] Extraction failed: {e}")
        return {
            "success": False,
            "documentType": doc_type,
            "confidence": conf,
            "extractedData": None,
            "message": f"Data extraction failed: {str(e)}"
        }
