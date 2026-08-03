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

        logger.info(f"[PDF Extractor] Processing {total_pages} page(s)...")

        for page_num in range(total_pages):
            try:
                page = doc[page_num]
                page_text = page.get_text("text")
                if page_text and page_text.strip():
                    text_parts.append(page_text)
            except Exception as e:
                logger.warning(
                    f"[PDF Extractor] Failed to extract page {page_num + 1}/{total_pages}: {e}"
                )
                # Skip corrupted pages, continue processing

        full_text = "\n".join(text_parts)

        logger.info(
            f"[PDF Extractor] Extracted {len(full_text)} chars from {len(text_parts)}/{total_pages} pages"
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

        for i in range(pages_to_check):
            page = doc[i]
            text = page.get_text("text").strip()
            word_count = len(text.split()) if text else 0

            if word_count < 10:
                scanned_pages += 1

        # If majority of sampled pages are scanned, treat entire PDF as scanned
        return scanned_pages > (pages_to_check / 2)

    finally:
        doc.close()
