"""
OCR Engine — PaddleOCR as primary, Gemini Vision as fallback.
Handles text extraction from images and scanned PDFs.
"""

import io
from typing import Optional

from app.core.logging import logger
from app.services.llm.ai_client import extract_json_multimodal
from app.services.llm.prompts import VISION_OCR_PROMPT

# Lazy-loaded PaddleOCR instance
_paddle_ocr = None
_paddle_available = False


def _get_paddle_ocr():
    """Lazily initialize PaddleOCR (avoids import cost at startup)."""
    global _paddle_ocr, _paddle_available
    if _paddle_ocr is not None:
        return _paddle_ocr

    try:
        from paddleocr import PaddleOCR

        _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        _paddle_available = True
        logger.info("[OCR Engine] PaddleOCR initialized successfully")
        return _paddle_ocr
    except Exception as e:
        _paddle_available = False
        logger.warning(f"[OCR Engine] PaddleOCR not available, will use Gemini Vision fallback: {e}")
        return None


async def extract_text_from_image_bytes(
    image_bytes: bytes,
    mime_type: str = "image/png",
) -> str:
    """
    Extract text from image bytes using PaddleOCR (primary) or Gemini Vision (fallback).

    Args:
        image_bytes: Raw image bytes (PNG, JPG, etc.)
        mime_type: MIME type of the image

    Returns:
        Extracted text string
    """
    # Try PaddleOCR first (primary)
    text = await _try_paddleocr(image_bytes)
    if text and len(text.strip()) > 20:
        logger.info(f"[OCR Engine] PaddleOCR extracted {len(text)} chars")
        return text

    # Fallback to Gemini Vision
    logger.info("[OCR Engine] Falling back to Gemini Vision OCR")
    return await _try_gemini_vision(image_bytes, mime_type)


async def _try_paddleocr(image_bytes: bytes) -> str:
    """Attempt OCR using PaddleOCR."""
    ocr = _get_paddle_ocr()
    if ocr is None:
        return ""

    try:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        img_array = np.array(image)

        result = ocr.ocr(img_array, cls=True)

        if not result or not result[0]:
            return ""

        lines: list[str] = []
        for line_result in result[0]:
            if line_result and len(line_result) > 1:
                text = line_result[1][0] if isinstance(line_result[1], (list, tuple)) else str(line_result[1])
                lines.append(text)

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[OCR Engine] PaddleOCR failed: {e}")
        return ""


async def _try_gemini_vision(image_bytes: bytes, mime_type: str) -> str:
    """Fallback: extract text using Gemini Vision API."""
    try:
        result = await extract_json_multimodal(
            system_prompt=VISION_OCR_PROMPT,
            text_content="Extract all text from this document image.",
            inline_parts=[{"mimeType": mime_type, "data": image_bytes}],
        )

        extracted_json = result.get("extractedJson", {})
        text = extracted_json.get("extractedText", "")

        if text:
            logger.info(f"[OCR Engine] Gemini Vision extracted {len(text)} chars")

        return text

    except Exception as e:
        logger.error(f"[OCR Engine] Gemini Vision OCR failed: {e}")
        return ""


async def extract_text_from_scanned_pdf(
    pdf_buffer: bytes,
    max_pages: int | None = None,
) -> str:
    """
    Extract text from a scanned PDF by converting pages to images and running OCR.

    Args:
        pdf_buffer: Raw PDF bytes
        max_pages: Maximum number of pages to process (None = all)

    Returns:
        Combined extracted text
    """
    from app.services.pdf.pdf_extractor import convert_pdf_page_to_image, get_pdf_page_count

    total_pages = get_pdf_page_count(pdf_buffer)
    pages_to_process = min(total_pages, max_pages) if max_pages else total_pages

    logger.info(f"[OCR Engine] Processing {pages_to_process}/{total_pages} scanned pages...")

    all_text: list[str] = []

    for page_num in range(pages_to_process):
        try:
            page_image = convert_pdf_page_to_image(pdf_buffer, page_num)
            page_text = await extract_text_from_image_bytes(page_image, "image/png")
            if page_text and page_text.strip():
                all_text.append(f"--- Page {page_num + 1} ---\n{page_text}")
        except Exception as e:
            logger.warning(f"[OCR Engine] Failed to OCR page {page_num + 1}: {e}")

    combined = "\n\n".join(all_text)
    logger.info(f"[OCR Engine] Total OCR output: {len(combined)} chars from {len(all_text)} pages")
    return combined
