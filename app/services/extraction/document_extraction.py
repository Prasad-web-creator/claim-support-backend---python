"""
Document Extraction Service — migrated from DocumentExtractionService.js.
Coordinates the extraction of raw text from PDFs, DOCX, and images.
Replaces the complex worker-thread architecture with a unified async pipeline.
"""

import io
import docx
from app.core.logging import logger
from app.services.pdf.pdf_extractor import (
    extract_text_from_pdf_buffer,
    is_scanned_pdf
)
from app.services.ocr.ocr_engine import (
    extract_text_from_image_bytes,
    extract_text_from_scanned_pdf
)


def _validate_extracted_text(text: str) -> bool:
    """
    Validates if the extracted text looks like meaningful content
    instead of just garbage or a single line of gibberish.
    """
    if not text or len(text.strip()) < 50:
        return False
        
    words = text.split()
    if len(words) < 5:
        return False
        
    # Check if text is mostly alphabetic characters
    # This catches cases where PDF extraction just returns unicode boxes or symbols
    alpha_chars = sum(1 for c in text if c.isalpha())
    if len(text) > 0 and (alpha_chars / len(text)) < 0.2:
        return False
        
    return True


async def extract_text_from_document(buffer: bytes, mime_type: str) -> str:
    """
    Unified extraction pipeline for all supported document types.
    """
    logger.info(f"[DocExtractor] Processing document of type: {mime_type}")
    text = ""
    
    try:
        # Route 1: Image -> OCR
        if mime_type.startswith("image/"):
            text = await extract_text_from_image_bytes(buffer, mime_type)
            
        # Route 2: DOCX -> Python-docx
        elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            doc = docx.Document(io.BytesIO(buffer))
            text = "\n".join([p.text for p in doc.paragraphs])
            
        # Route 3: PDF -> PyMuPDF (text-based) or PaddleOCR (scanned)
        elif mime_type == "application/pdf":
            if is_scanned_pdf(buffer):
                logger.info("[DocExtractor] PDF is scanned (image-based). Routing to PaddleOCR.")
                text = await extract_text_from_scanned_pdf(buffer)
            else:
                logger.info("[DocExtractor] PDF is text-based. Routing to PyMuPDF.")
                text = extract_text_from_pdf_buffer(buffer)
                    
        # Route 4: Fallback for plain text or unknown
        else:
            text = buffer.decode('utf-8', errors='ignore')
            
    except Exception as e:
        logger.error(f"[DocExtractor] Failed to extract text: {e}")
        
    if not _validate_extracted_text(text):
        logger.warning("[DocExtractor] Final output failed validation. Document might be unreadable.")
        
    return text
