"""
OCR Engine — Optimized cloud-first & local-fallback pipeline.
Primary: Google Gemini Flash Vision (Cloud, ultra-fast 2-5s, low RAM, Railway compatible).
Secondary: Local PaddleOCR (Parallel process pool, offline fallback).

Includes rich developer debug logging to easily diagnose stalls, bottlenecks, and error causes.
"""

import io
import os
import time
import asyncio
import concurrent.futures
from typing import Optional

from app.core.logging import logger
from app.core.config import get_settings
from app.services.llm.ai_client import extract_text_multimodal
from app.services.llm.prompts import DIRECT_OCR_SYSTEM_PROMPT

# ── Main-process single-image OCR fallback ─────────────────────────────────────
_paddle_ocr = None
_paddle_attempted = False

# ── Parallel PDF-page OCR via process pool fallback ────────────────────────────
_ocr_executor: concurrent.futures.ProcessPoolExecutor | None = None
_MAX_OCR_WORKERS = 2  # 2 workers when Paddle fallback runs

# ── Global semaphore: only ONE local Paddle OCR runs at a time to prevent OOM ──
_ocr_semaphore: asyncio.Semaphore | None = None


def _get_ocr_semaphore() -> asyncio.Semaphore:
    """Lazily create the global OCR semaphore (must happen inside an event loop)."""
    global _ocr_semaphore
    if _ocr_semaphore is None:
        _ocr_semaphore = asyncio.Semaphore(1)
    return _ocr_semaphore


# ─────────────────────────────────────────────────────────────────────────────
# PaddleOCR Lazy Initializers (Fallback only)
# ─────────────────────────────────────────────────────────────────────────────

def _get_paddle_ocr():
    """Lazily load PaddleOCR in the main process (single-image fallback calls only)."""
    global _paddle_ocr, _paddle_attempted
    if _paddle_attempted:
        return _paddle_ocr
    _paddle_attempted = True

    os.environ.setdefault("FLAGS_logtostderr", "0")
    os.environ.setdefault("GLOG_v", "0")
    os.environ.setdefault("PADDLE_LOG_LEVEL", "ERROR")

    try:
        from paddleocr import PaddleOCR
        t0 = time.time()
        logger.info("[OCR Engine] [DEBUG] [PaddleOCR] Loading local PaddleOCR model weights into memory...")
        _paddle_ocr = PaddleOCR(
            use_angle_cls=False,
            lang="en",
            show_log=False,
            cpu_threads=2,
            det_db_score_mode="fast",
        )
        logger.info(
            f"[OCR Engine] [DEBUG] [PaddleOCR] Model loaded ready in {time.time() - t0:.2f}s"
        )
        return _paddle_ocr
    except Exception as e:
        logger.warning(
            f"[OCR Engine] [DEBUG] [PaddleOCR] Failed to load local PaddleOCR: {type(e).__name__}: {e}. "
            "Local OCR fallback will be unavailable."
        )
        return None


def _get_ocr_executor() -> concurrent.futures.ProcessPoolExecutor | None:
    """Get (or lazily create) the shared process pool for parallel Paddle fallback PDF OCR."""
    global _ocr_executor
    if _ocr_executor is None:
        try:
            from app.services.ocr.ocr_worker import init_worker
            logger.info(
                f"[OCR Engine] [DEBUG] [PaddleOCR] Spawning ProcessPoolExecutor with {_MAX_OCR_WORKERS} workers..."
            )
            _ocr_executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=_MAX_OCR_WORKERS,
                initializer=init_worker,
            )
        except Exception as e:
            logger.warning(
                f"[OCR Engine] [DEBUG] [PaddleOCR] Could not create ProcessPoolExecutor: {type(e).__name__}: {e}"
            )
            return None
    return _ocr_executor


# ─────────────────────────────────────────────────────────────────────────────
# Local PaddleOCR Fallback Functions
# ─────────────────────────────────────────────────────────────────────────────

def _sync_single_image_ocr(image_bytes: bytes, doc_label: str = "Image") -> str:
    """Blocking OCR on one image using local PaddleOCR."""
    ocr = _get_paddle_ocr()
    if ocr is None:
        logger.warning(f"[OCR Engine] [DEBUG] [{doc_label}] PaddleOCR is unavailable; skipping fallback.")
        return ""
    try:
        import numpy as np
        from PIL import Image

        t0 = time.time()
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        
        w, h = img.size
        logger.info(f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Running inference on image ({w}x{h} px)...")
        
        result = ocr.ocr(np.array(img), cls=False)
        if not result or not result[0]:
            logger.warning(f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] No text detected in image ({time.time()-t0:.2f}s).")
            return ""
            
        lines = []
        for box in result[0]:
            if box and len(box) > 1:
                rec = box[1]
                txt = rec[0] if isinstance(rec, (list, tuple)) else str(rec)
                if txt and txt.strip():
                    lines.append(txt.strip())
                    
        extracted = "\n".join(lines)
        logger.info(
            f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Detected {len(lines)} lines ({len(extracted)} chars) in {time.time()-t0:.2f}s"
        )
        return extracted
    except Exception as e:
        logger.error(
            f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Single-image OCR error: {type(e).__name__}: {e}",
            exc_info=True
        )
        return ""


async def _do_parallel_ocr_paddle(
    pdf_buffer: bytes,
    pages_to_process: int,
    render_dpi: int,
    t_start: float,
    worker_ocr_page,
    doc_label: str = "Scanned PDF",
) -> str:
    """Inner PaddleOCR multi-page PDF execution with detailed batch debug logs."""
    from app.services.pdf.pdf_extractor import render_all_pages_as_bytes

    BATCH_SIZE = 8  # pages per batch — limits peak memory

    t_render = time.time()
    try:
        all_images: list[bytes] = await asyncio.to_thread(
            render_all_pages_as_bytes, pdf_buffer, render_dpi, pages_to_process
        )
    except Exception as e:
        logger.error(f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] PDF rendering failed: {e}")
        return ""

    logger.info(
        f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Rendered {len(all_images)} pages in {time.time()-t_render:.2f}s (DPI={render_dpi})"
    )

    executor = _get_ocr_executor()
    if executor is None:
        logger.error(f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Process pool unavailable")
        return ""

    t_ocr = time.time()
    all_results: list[tuple[int, str]] = []
    total_batches = (len(all_images) + BATCH_SIZE - 1) // BATCH_SIZE

    for b_idx, batch_start in enumerate(range(0, len(all_images), BATCH_SIZE)):
        t_batch = time.time()
        batch = all_images[batch_start : batch_start + BATCH_SIZE]
        futures = [
            asyncio.wrap_future(executor.submit(worker_ocr_page, (batch_start + i, img)))
            for i, img in enumerate(batch)
        ]
        batch_results = await asyncio.gather(*futures, return_exceptions=True)

        for r in batch_results:
            if isinstance(r, Exception):
                logger.warning(f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Page task exception: {r}")
            elif isinstance(r, tuple) and r[1]:
                all_results.append(r)

        batch_elapsed = time.time() - t_batch
        logger.info(
            f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] Batch {b_idx + 1}/{total_batches} "
            f"({batch_start + len(futures)}/{len(all_images)} pages) finished in {batch_elapsed:.2f}s "
            f"(avg {batch_elapsed/max(1, len(futures)):.2f}s/page)"
        )
        batch.clear()

    elapsed_ocr = time.time() - t_ocr
    all_results.sort(key=lambda x: x[0])
    total_chars = sum(len(t) for _, t in all_results)

    logger.info(
        f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] All {len(all_images)} pages done in {elapsed_ocr:.2f}s "
        f"| Extracted {total_chars} chars across {len(all_results)} pages (Total time: {time.time()-t_start:.2f}s)"
    )

    page_texts = [f"--- Page {idx + 1} ---\n{text}" for idx, text in all_results]
    return "\n\n".join(page_texts)


async def _extract_text_from_scanned_pdf_paddle(
    pdf_buffer: bytes,
    max_pages: int | None = None,
    doc_label: str = "Scanned PDF",
) -> str:
    """Fallback: Local parallel PaddleOCR pipeline."""
    try:
        from app.services.pdf.pdf_extractor import get_pdf_page_count
        from app.services.ocr.ocr_worker import ocr_page as worker_ocr_page
    except Exception as e:
        logger.error(f"[OCR Engine] [DEBUG] [PaddleOCR] Failed to load worker components: {e}")
        return ""

    t_start = time.time()
    total_pages = get_pdf_page_count(pdf_buffer)
    pages_to_process = min(total_pages, max_pages) if max_pages else total_pages

    render_dpi = 72 if pages_to_process > 10 else 90

    logger.info(
        f"[OCR Engine] [DEBUG] ↻ [FALLBACK ACTIVATED] [{doc_label}] Starting local PaddleOCR: "
        f"{pages_to_process}/{total_pages} pages | Workers: {_MAX_OCR_WORKERS} | DPI: {render_dpi}"
    )

    sem = _get_ocr_semaphore()
    async with sem:
        logger.info(f"[OCR Engine] [DEBUG] [PaddleOCR] [{doc_label}] OCR semaphore acquired — processing pages")
        return await _do_parallel_ocr_paddle(
            pdf_buffer, pages_to_process, render_dpi, t_start, worker_ocr_page, doc_label=doc_label
        )


# ─────────────────────────────────────────────────────────────────────────────
# Primary: Gemini Flash Vision OCR
# ─────────────────────────────────────────────────────────────────────────────

async def extract_text_from_image_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    doc_label: str = "Image",
) -> str:
    """Extract text from a single image via Gemini Flash Vision (~1-2s)."""
    t0 = time.time()
    settings = get_settings()
    model = settings.AI_VISION_MODEL or settings.AI_MODEL
    size_kb = len(image_bytes) / 1024.0
    
    logger.info(
        f"[OCR Engine] [DEBUG] ☁ [GEMINI PRIMARY] [{doc_label}] Dispatched to {model} | Size: {size_kb:.1f} KB | MIME: {mime_type}"
    )
    try:
        text = await extract_text_multimodal(
            system_prompt=DIRECT_OCR_SYSTEM_PROMPT,
            text_content="Extract all readable text from this document image exactly as it appears.",
            inline_parts=[{"mimeType": mime_type, "data": image_bytes}],
            model_name=model,
            max_tokens=8192,
        )
        elapsed = time.time() - t0
        if text and len(text.strip()) >= 15:
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
            f"[OCR Engine] [DEBUG] ✖ [GEMINI ERROR] [{doc_label}] API call failed after {elapsed:.2f}s: {type(e).__name__}: {e}"
        )
        return ""


async def extract_text_from_scanned_pdf_gemini(
    pdf_buffer: bytes,
    doc_label: str = "Scanned PDF",
    max_pages: int = 6,
) -> str:
    """Extract scanned PDF via Gemini Flash Vision with smart page bounding (~4-10s)."""
    from app.services.pdf.pdf_extractor import optimize_pdf_for_vision
    t0 = time.time()
    settings = get_settings()
    model = settings.AI_VISION_MODEL or settings.AI_MODEL

    # Pre-compress and bound scanned PDF to key pages (e.g. Schedule & Benefits)
    payload_buffer = await asyncio.to_thread(optimize_pdf_for_vision, pdf_buffer, 100, 2 * 1024 * 1024, max_pages)
    size_kb = len(payload_buffer) / 1024.0

    logger.info(
        f"[OCR Engine] [DEBUG] ☁ [GEMINI PRIMARY] [{doc_label}] Sending optimized PDF to {model} | Size: {size_kb:.1f} KB"
    )
    try:
        text = await extract_text_multimodal(
            system_prompt=DIRECT_OCR_SYSTEM_PROMPT,
            text_content="Extract all readable text from all pages of this document exactly as it appears.",
            inline_parts=[{"mimeType": "application/pdf", "data": payload_buffer}],
            model_name=model,
            max_tokens=4096,
        )
        elapsed = time.time() - t0
        if text and len(text.strip()) >= 30:
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
            f"[OCR Engine] [DEBUG] ✖ [GEMINI ERROR] [{doc_label}] PDF API call failed after {elapsed:.2f}s: {type(e).__name__}: {e}"
        )
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Public API: High-Level Document OCR Functions (Primary + Secondary Fallback)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Public API: High-Level Document OCR Functions (Smart Hybrid Routing)
# ─────────────────────────────────────────────────────────────────────────────

async def extract_text_from_image_bytes(
    image_bytes: bytes,
    mime_type: str = "image/png",
    doc_label: str = "Image",
) -> str:
    """
    Extract text from a single image.
    PRIMARY: Local PaddleOCR (100% Free, ₹0.00).
    FALLBACK: Gemini Flash Vision (Cloud).
    """
    # 1. Primary: Local PaddleOCR
    t0 = time.time()
    logger.info(f"[OCR Engine] [DEBUG] 🚀 [PADDLE PRIMARY] [{doc_label}] Running local PaddleOCR...")
    paddle_text = await asyncio.to_thread(_sync_single_image_ocr, image_bytes, doc_label)
    if paddle_text and len(paddle_text.strip()) >= 15:
        logger.info(
            f"[OCR Engine] [DEBUG] ✔ [PADDLE SUCCESS] [{doc_label}] Extracted {len(paddle_text)} chars in {time.time()-t0:.2f}s (₹0.00 Cost)"
        )
        return paddle_text

    # 2. Secondary: Gemini Flash Vision Fallback
    logger.warning(
        f"[OCR Engine] [DEBUG] ↻ [FALLBACK TO CLOUD] [{doc_label}] PaddleOCR returned insufficient text; "
        "initiating Gemini Flash Vision fallback..."
    )
    text = await extract_text_from_image_gemini(image_bytes, mime_type, doc_label=doc_label)
    if text and len(text.strip()) > 0:
        return text

    return paddle_text or text or ""


async def extract_text_from_scanned_pdf(
    pdf_buffer: bytes,
    max_pages: int | None = None,
    doc_label: str = "Scanned PDF",
) -> str:
    """
    Extract text from a scanned/image-based multi-page PDF using Smart Hybrid Routing:
    - If pages <= 3 (1-3 pages): PRIMARY is Local PaddleOCR (Free ₹0.00), FALLBACK is Gemini Cloud.
    - If pages > 3 (4+ pages): PRIMARY is Gemini Cloud Vision (Fast batch OCR), FALLBACK is PaddleOCR.
    """
    from app.services.pdf.pdf_extractor import get_pdf_page_count
    total_pages = get_pdf_page_count(pdf_buffer)
    pages_to_process = min(total_pages, max_pages) if max_pages else total_pages

    if pages_to_process <= 3:
        # 1-3 pages: Use Local PaddleOCR Primary (100% Free)
        logger.info(
            f"[OCR Engine] [DEBUG] 🚀 [PADDLE PRIMARY (<=3 pages)] [{doc_label}] Processing {pages_to_process} pages locally (₹0.00 Cost)"
        )
        paddle_text = await _extract_text_from_scanned_pdf_paddle(pdf_buffer, max_pages, doc_label=doc_label)
        if paddle_text and len(paddle_text.strip()) >= 30:
            from app.services.llm.ai_client import get_current_cost_tracker
            tracker = get_current_cost_tracker()
            if tracker:
                tracker.record_step(
                    operation=f"{doc_label} OCR ({pages_to_process} pp)",
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    cost_inr=0.0,
                    engine="PaddleOCR",
                )
            return paddle_text

        # Fallback to Gemini Cloud if Paddle yielded insufficient text
        logger.warning(
            f"[OCR Engine] [DEBUG] ↻ [FALLBACK TO CLOUD] [{doc_label}] PaddleOCR returned insufficient text for {pages_to_process} pages; "
            "initiating Gemini Cloud Vision fallback..."
        )
        return await extract_text_from_scanned_pdf_gemini(pdf_buffer, doc_label=doc_label)
    else:
        # 4+ pages: Use Gemini Cloud Vision Primary (Fast multi-page parallel processing)
        logger.info(
            f"[OCR Engine] [DEBUG] ☁ [GEMINI PRIMARY (4+ pages)] [{doc_label}] Processing {pages_to_process} pages via Gemini Cloud OCR"
        )
        text = await extract_text_from_scanned_pdf_gemini(pdf_buffer, doc_label=doc_label)
        if text and len(text.strip()) >= 50:
            return text

        # Fallback to Local PaddleOCR Process Pool
        logger.warning(
            f"[OCR Engine] [DEBUG] ↻ [FALLBACK TO LOCAL] [{doc_label}] Gemini Cloud Vision returned insufficient text; "
            "initiating local PaddleOCR process pool fallback..."
        )
        return await _extract_text_from_scanned_pdf_paddle(pdf_buffer, max_pages, doc_label=doc_label)


