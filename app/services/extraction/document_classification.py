"""
Document Classification Service — migrated from DocumentClassificationService.js.
Classifies document type based on text or image buffers.
"""

from app.services.llm.ai_client import extract_json_with_retry, extract_json_multimodal
from app.services.llm.prompts import CLASSIFICATION_PROMPT, CLASSIFICATION_LABELS


async def classify_document_from_text(text: str) -> dict:
    """Classify a document from its extracted text."""
    if not text or len(text.strip()) < 20:
        return {
            "documentType": "Other",
            "confidence": 0.0,
            "reasoning": "Insufficient text for classification."
        }

    snippet = text[:4000] # Use first 4k chars for fast classification
    
    try:
        result = await extract_json_with_retry(CLASSIFICATION_PROMPT, snippet, max_tokens=150)
        extracted = result.get("extractedJson", {})
        
        doc_type = extracted.get("documentType", "Other")
        if doc_type not in CLASSIFICATION_LABELS:
            doc_type = "Other"
            
        confidence = extracted.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
            
        return {
            "documentType": doc_type,
            "confidence": float(confidence),
            "reasoning": extracted.get("reasoning", "")
        }
    except Exception as e:
        return {
            "documentType": "Other",
            "confidence": 0.0,
            "reasoning": f"Classification failed: {e}"
        }


async def classify_document_from_buffer(buffer: bytes, mime_type: str) -> dict:
    """Classify a document from a raw binary buffer (image or PDF)."""
    try:
        result = await extract_json_multimodal(
            system_prompt=CLASSIFICATION_PROMPT,
            text_content="",
            inline_parts=[{"mimeType": mime_type, "data": buffer}],
            max_tokens=150
        )
        
        extracted = result.get("extractedJson", {})
        
        doc_type = extracted.get("documentType", "Other")
        if doc_type not in CLASSIFICATION_LABELS:
            doc_type = "Other"
            
        confidence = extracted.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
            
        return {
            "documentType": doc_type,
            "confidence": float(confidence),
            "reasoning": extracted.get("reasoning", "")
        }
    except Exception as e:
        return {
            "documentType": "Other",
            "confidence": 0.0,
            "reasoning": f"Classification failed: {e}"
        }
