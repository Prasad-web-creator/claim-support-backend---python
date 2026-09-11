"""
PDF Extractor — migrated from the PDF parsing cascade in DocumentExtractionService.js.
Uses PyMuPDF (fitz) as the single, reliable PDF text extraction engine.
Replaces the Node.js cascade of pdf-parse, pdfjs-dist, pdf2json, and worker threads.
"""

import fitz  # PyMuPDF
from app.core.logging import logger


def extract_text_from_pdf_buffer(buffer: bytes) -> str:
    """
    Extract text from a PDF buffer using PyMuPDF.
    Processes page-by-page, handles rotated pages, and recovers from corrupted pages.

    Args:
        buffer: Raw PDF file bytes.

    Returns:
        Extracted text string.
    """
    doc = None
    try:
        doc = fitz.open(stream=buffer, filetype="pdf")
        text_parts: list[str] = []
        total_pages = doc.page_count

        logger.debug(f"[PDF] Parsing text on {total_pages} page(s) with PyMuPDF...")

        for page_num in range(total_pages):
            try:
                page = doc[page_num]
                page_text = page.get_text("text")
                if page_text and page_text.strip():
                    text_parts.append(page_text)
            except Exception as e:
                logger.debug(
                    f"[PDF] Failed to parse text on page {page_num + 1}/{total_pages}: {e}"
                )
                # Skip corrupted pages, continue processing

        full_text = "\n".join(text_parts)

        logger.debug(
            f"[PDF] Extracted {len(full_text)} chars ({len(full_text.split())} words) from {len(text_parts)}/{total_pages} pages"
        )

        from app.services.llm.ai_client import get_current_cost_tracker
        tracker = get_current_cost_tracker()
        if tracker:
            tracker.record_step(
                operation=f"Native Text Extraction ({total_pages} pp)",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                cost_inr=0.0,
                engine="PyMuPDF",
            )

        return full_text

    except Exception as e:
        logger.error(f"[PDF Extractor] Fatal error processing PDF: {e}")
        raise

    finally:
        if doc:
            doc.close()


def extract_text_from_pdf_path(file_path: str) -> str:
    """
    Extract text from a PDF file on disk using PyMuPDF.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Extracted text string.
    """
    doc = None
    try:
        doc = fitz.open(file_path)
        text_parts: list[str] = []

        for page_num in range(doc.page_count):
            try:
                page = doc[page_num]
                page_text = page.get_text("text")
                if page_text and page_text.strip():
                    text_parts.append(page_text)
            except Exception as e:
                logger.warning(
                    f"[PDF Extractor] Failed to extract page {page_num + 1}: {e}"
                )

        return "\n".join(text_parts)

    finally:
        if doc:
            doc.close()


def get_pdf_page_count(buffer: bytes) -> int:
    """Get the number of pages in a PDF."""
    doc = fitz.open(stream=buffer, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def render_all_pages_as_bytes(
    buffer: bytes,
    dpi: int = 90,
    max_pages: int | None = None,
) -> list[bytes]:
    """
    Render ALL pages of a PDF to PNG bytes in a SINGLE document open.

    Args:
        buffer:    Raw PDF bytes.
        dpi:       Rendering resolution (default 90).
        max_pages: Maximum pages to render (None = all).

    Returns:
        List of PNG-encoded page images in page order.
    """
    doc = fitz.open(stream=buffer, filetype="pdf")
    images: list[bytes] = []
    try:
        n = min(doc.page_count, max_pages) if max_pages else doc.page_count
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i in range(n):
            # alpha=False → RGB pixmap (no RGBA→RGB conversion needed later)
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            images.append(pix.tobytes("png"))
    finally:
        doc.close()
    return images


def convert_pdf_page_to_image(buffer: bytes, page_num: int, dpi: int = 300) -> bytes:
    """
    Convert a single PDF page to a PNG image for OCR processing.

    Args:
        buffer: Raw PDF file bytes.
        page_num: Page number (0-indexed).
        dpi: Resolution for rendering.

    Returns:
        PNG image bytes.
    """
    doc = fitz.open(stream=buffer, filetype="pdf")
    try:
        page = doc[page_num]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
    finally:
        doc.close()


def is_scanned_pdf(buffer: bytes, sample_pages: int = 3) -> bool:
    """
    Detect if a PDF is scanned (image-based) by checking word density on sample pages.
    A page with fewer than 10 words is considered scanned.
    """
    doc = fitz.open(stream=buffer, filetype="pdf")
    try:
        pages_to_check = min(sample_pages, doc.page_count)
        scanned_pages = 0
        word_counts = []

        for i in range(pages_to_check):
            page = doc[i]
            text = page.get_text("text").strip()
            word_count = len(text.split()) if text else 0
            word_counts.append(f"p{i+1}:{word_count}w")

            if word_count < 10:
                scanned_pages += 1

        is_scanned = scanned_pages > (pages_to_check / 2)
        logger.debug(
            f"[PDF] Format Analysis: {doc.page_count} total pages | "
            f"Sampled {pages_to_check} pages: [{', '.join(word_counts)}] | "
            f"Result: {'SCANNED (Image-based)' if is_scanned else 'NATIVE (Text-based)'}"
        )
        return is_scanned

    finally:
        doc.close()


def get_pdf_page_count(buffer: bytes) -> int:
    """Return the number of pages in a PDF buffer."""
    doc = fitz.open(stream=buffer, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def optimize_pdf_for_vision(
    buffer: bytes,
    target_dpi: int = 100,
    max_size_bytes: int = 2 * 1024 * 1024,
    max_pages: int | None = None,
) -> bytes:
    """
    Optimizes and compresses scanned PDFs before cloud multimodal transmission.
    - Limits scanned pages to key pages (e.g. first 6 pages for policy schedule/benefits).
    - Downscales heavy 300+ DPI bitmap scans to 100 DPI JPEG in memory.
    - Shrinks 15MB–50MB PDFs to ~100KB–800KB in <0.5s, allowing Gemini to process
      and transcribe the document in 5–12 seconds rather than minutes.
    """
    import time
    t0 = time.time()
    orig_mb = len(buffer) / (1024 * 1024)
    doc = fitz.open(stream=buffer, filetype="pdf")
    try:
        total_pages = doc.page_count
        pages_to_process = min(total_pages, max_pages) if max_pages else total_pages

        # If already small and no page truncation needed, return as-is
        if len(buffer) <= max_size_bytes and pages_to_process == total_pages:
            return buffer

        new_doc = fitz.open()
        zoom = target_dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        for i in range(pages_to_process):
            page = doc[i]
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes = pix.tobytes("jpeg", jpg_quality=75)
            new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(page.rect, stream=img_bytes)

        compressed = new_doc.tobytes(deflate=True, garbage=4)
        new_doc.close()
        new_kb = len(compressed) / 1024.0
        logger.info(
            f"[PDF Extractor] [DEBUG] ⚡ Optimized scanned PDF ({pages_to_process}/{total_pages} pp): "
            f"{orig_mb:.1f} MB -> {new_kb:.1f} KB in {time.time()-t0:.2f}s (DPI={target_dpi})"
        )
        return compressed
    except Exception as e:
        logger.warning(f"[PDF Extractor] [DEBUG] PDF optimization skipped due to: {e}")
        return buffer
    finally:
        doc.close()

