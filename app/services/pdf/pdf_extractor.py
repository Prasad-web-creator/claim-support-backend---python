"""Detect image-based PDF uploads."""

import fitz

from app.core.logging import logger


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
