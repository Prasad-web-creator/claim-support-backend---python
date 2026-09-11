"""
OCR Engine — Cloud-first Gemini Vision OCR Pipeline.
Uses Google Gemini Flash Vision for fast (1-2s), zero-server-RAM text extraction
from images and scanned documents. Fully compatible with Railway Free Tier (512MB RAM).
"""

import time
import asyncio
from typing import Optional

from app.core.logging import logger
from app.core.config import get_settings
from app.services.llm.ai_client import extract_text_multimodal
from app.services.llm.prompts import DIRECT_OCR_SYSTEM_PROMPT


async def extract_text_from_image_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    doc_label: str = "Image",
) -> str:
    """
    Extract readable text from an image using Google Gemini Flash Vision (~1-2s).
    Requires zero local ML model memory overhead.
    """
    t0 = time.time()
    settings = get_settings()
    model = settings.AI_VISION_MODEL or settings.AI_MODEL
    size_kb = len(image_bytes) / 1024.0

    logger.info(
        f"[OCR Engine] [DEBUG] ☁ [GEMINI VISION] [{doc_label}] Dispatched to {model} | Size: {size_kb:.1f} KB | MIME: {mime_type}"
    )
    try:
        text = await extract_text_multimodal(
            system_prompt=DIRECT_OCR_SYSTEM_PROMPT,
            text_content="Extract all readable text from this document image exactly as it appears. Maintain line structure and headings.",
            inline_parts=[{"mimeType": mime_type, "data": image_bytes}],
            model_name=model,
            max_tokens=8192,
        )
        elapsed = time.time() - t0
        if text and len(text.strip()) >= 10:
            logger.info(
                f"[OCR Engine] [DEBUG] ✔ [GEMINI SUCCESS] [{doc_label}] Extracted {len(text)} chars ({len(text.split())} words) in {elapsed:.2f}s"
            )
            return text

        logger.warning(
            f"[OCR Engine] [DEBUG] ✖ [GEMINI EMPTY] [{doc_label}] Model returned minimal/empty text ({len(text) if text else 0} chars) in {elapsed:.2f}s"
        )
        return ""
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(
            f"[OCR Engine] [DEBUG] ✖ [GEMINI ERROR] [{doc_label}] Vision API call failed after {elapsed:.2f}s: {type(e).__name__}: {e}"
        )
        return ""


async def extract_text_from_scanned_pdf_gemini(
    pdf_buffer: bytes,
    doc_label: str = "Scanned PDF",
    max_pages: int = 6,
) -> str:
    """
    Extract scanned PDF text via Gemini Flash Vision with smart page bounding (~3-6s).
    """
    from app.services.pdf.pdf_extractor import optimize_pdf_for_vision

    t0 = time.time()
    settings = get_settings()
    model = settings.AI_VISION_MODEL or settings.AI_MODEL

    # Pre-compress and bound scanned PDF to key pages
    payload_buffer = await asyncio.to_thread(optimize_pdf_for_vision, pdf_buffer, 100, 2 * 1024 * 1024, max_pages)
    size_kb = len(payload_buffer) / 1024.0

    logger.info(
        f"[OCR Engine] [DEBUG] ☁ [GEMINI PDF OCR] [{doc_label}] Sending optimized PDF to {model} | Size: {size_kb:.1f} KB"
    )
    try:
        text = await extract_text_multimodal(
            system_prompt=DIRECT_OCR_SYSTEM_PROMPT,
            text_content="Extract all readable text from all pages of this document exactly as it appears.",
            inline_parts=[{"mimeType": "application/pdf", "data": payload_buffer}],
            model_name=model,
            max_tokens=8192,
        )
        elapsed = time.time() - t0
        if text and len(text.strip()) >= 20:
            logger.info(
                f"[OCR Engine] [DEBUG] ✔ [GEMINI SUCCESS] [{doc_label}] Extracted {len(text)} chars ({len(text.split())} words) in {elapsed:.2f}s"
            )
            return text

        logger.warning(
            f"[OCR Engine] [DEBUG] ✖ [GEMINI EMPTY] [{doc_label}] Model returned minimal text ({len(text) if text else 0} chars) in {elapsed:.2f}s"
        )
        return ""
    except Exception as e:
        elapsed = time.time() - t0
        logger.error(
            f"[OCR Engine] [DEBUG] ✖ [GEMINI ERROR] [{doc_label}] PDF Vision API call failed after {elapsed:.2f}s: {type(e).__name__}: {e}"
        )
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Public API: High-Level Document OCR Functions (Pure Gemini Vision)
# ─────────────────────────────────────────────────────────────────────────────

async def extract_text_from_image_bytes(
    image_bytes: bytes,
    mime_type: str = "image/png",
    doc_label: str = "Image",
) -> str:
    """
    Public entrypoint: Extract text from a single image (JPG, PNG, WebP) using Gemini Flash Vision.
    """
    return await extract_text_from_image_gemini(image_bytes, mime_type, doc_label=doc_label)


async def extract_text_from_scanned_pdf(
    pdf_buffer: bytes,
    max_pages: int | None = None,
    doc_label: str = "Scanned PDF",
) -> str:
    """
    Public entrypoint: Extract text from a scanned/image-based multi-page PDF using Gemini Flash Vision.
    """
    limit = max_pages or 6
    return await extract_text_from_scanned_pdf_gemini(pdf_buffer, doc_label=doc_label, max_pages=limit)
