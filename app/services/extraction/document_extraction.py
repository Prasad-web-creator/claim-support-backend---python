"""
Document Extraction Service — migrated from DocumentExtractionService.js.
Coordinates the extraction of raw text from PDFs, DOCX, and images.
Replaces the complex worker-thread architecture with a unified async pipeline.
"""

import time
import io
import docx
from app.core.logging import logger
from app.services.pdf.pdf_extractor import (
    extract_text_from_pdf_buffer,
    is_scanned_pdf,
    get_pdf_page_count
)
from app.services.ocr.ocr_engine import (
    extract_text_from_image_bytes,
    extract_text_from_scanned_pdf
)


def _validate_extracted_text(text: str, doc_label: str = "Document") -> bool:
    """
    Validates if the extracted text looks like meaningful content
    instead of just garbage or a single line of gibberish.
    Logs developer diagnostics explaining the exact issue if validation fails.
    """
    if not text or len(text.strip()) == 0:
        logger.warning(f"[DocExtractor] [DEBUG] [{doc_label}] Validation Failed: Text is completely empty.")
        return False

    if len(text.strip()) < 50:
        logger.warning(
            f"[DocExtractor] [DEBUG] [{doc_label}] Validation Failed: Text too short "
            f"({len(text.strip())} chars < 50 threshold). Content: '{text.strip()}'"
        )
        return False
        
    words = text.split()
    if len(words) < 5:
        logger.warning(
            f"[DocExtractor] [DEBUG] [{doc_label}] Validation Failed: Too few words ({len(words)} < 5 threshold)."
        )
        return False
        
    # Check if text is mostly alphabetic characters
    # This catches cases where PDF extraction just returns unicode boxes or symbols
    alpha_chars = sum(1 for c in text if c.isalpha())
    alpha_ratio = alpha_chars / len(text)
    if alpha_ratio < 0.2:
        logger.warning(
            f"[DocExtractor] [DEBUG] [{doc_label}] Validation Failed: Low alphabetic ratio "
            f"({alpha_ratio:.1%} < 20.0%). Document likely contains unparseable symbols or font encoding issues."
        )
        return False
        
    return True


async def extract_text_from_document(
    buffer: bytes,
    mime_type: str,
    doc_label: str = "Document",
) -> str:
    """
    Unified extraction pipeline with developer debug logging for all supported document types.
    """
    t_start = time.time()
    size_kb = len(buffer) / 1024.0
    logger.info(
        f"[Document] Extracting text from {doc_label} ({size_kb:.1f} KB, {mime_type})..."
    )
    text = ""
    
    try:
        # Route 1: Image -> OCR
        if mime_type.startswith("image/"):
            logger.debug(f"[Document] {doc_label} route: Image -> Gemini Flash Vision OCR")
            text = await extract_text_from_image_bytes(buffer, mime_type, doc_label=doc_label)
            
        # Route 2: DOCX -> Python-docx
        elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            logger.debug(f"[Document] {doc_label} route: DOCX -> python-docx parser")
            doc = docx.Document(io.BytesIO(buffer))
            text = "\n".join([p.text for p in doc.paragraphs])
            
        # Route 3: PDF -> PyMuPDF (text-based) or Gemini Flash Vision OCR (scanned)
        elif mime_type == "application/pdf":
            page_count = get_pdf_page_count(buffer)
            logger.debug(f"[Document] {doc_label} route: PDF ({page_count} pages)")
            
            if is_scanned_pdf(buffer):
                logger.debug(f"[Document] {doc_label} is scanned image PDF -> Routing to Gemini Flash Vision OCR")
                text = await extract_text_from_scanned_pdf(buffer, doc_label=doc_label)
            else:
                logger.debug(f"[Document] {doc_label} is native text PDF -> Fast PyMuPDF extraction")
                text = extract_text_from_pdf_buffer(buffer)
                if not text or len(text.strip()) < 50:
                    logger.debug(f"[Document] PyMuPDF extracted minimal text; falling back to Gemini Vision OCR")
                    text = await extract_text_from_scanned_pdf(buffer, doc_label=doc_label)
                    
        # Route 4: Fallback for plain text or unknown
        else:
            logger.debug(f"[Document] {doc_label} route: Plaintext fallback")
            text = buffer.decode("utf-8", errors="ignore")
            
    except Exception as e:
        logger.error(
            f"[Document] Extraction failed for {doc_label}: {e}",
            exc_info=True
        )
        
    elapsed = time.time() - t_start
    is_valid = _validate_extracted_text(text, doc_label=doc_label)
    
    if is_valid:
        logger.info(
            f"[Document] Extracted {len(text)} characters ({len(text.split())} words) from {doc_label} in {elapsed:.2f}s"
        )
    else:
        logger.warning(
            f"[Document] Completed with quality warnings for {doc_label} in {elapsed:.2f}s ({len(text)} chars extracted)"
        )
        
    return text
