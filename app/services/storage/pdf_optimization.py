"""Optimize image-only scans while preserving PDF structure and text."""

from io import BytesIO
from math import ceil, hypot

import fitz
from PIL import Image, ImageChops

from app.core.config import Settings
from app.core.logging import logger
from app.services.pdf.pdf_access import serialized_pdf


def _is_neutral(image: Image.Image) -> bool:
    red, green, blue = image.split()
    return all(
        ImageChops.difference(first, second).getextrema()[1] == 0
        for first, second in ((red, green), (green, blue), (red, blue))
    )


def _scan_images(document: fitz.Document, settings: Settings):
    candidates = {}
    protected = set()
    for page in document:
        has_text = bool(page.get_text().strip())
        for xref, mask, width, height, *_ in page.get_images(full=True):
            if has_text or mask or width * height > settings.PDF_COMPRESSION_MAX_PAGE_PIXELS:
                protected.add(xref)
                continue

            instances = page.get_image_rects(xref, transform=True)
            if not instances:
                continue
            for rect, matrix in instances:
                if (rect & page.rect).get_area() < page.rect.get_area() * 0.75:
                    protected.add(xref)
                    continue
                target_width = ceil(hypot(matrix.a, matrix.b) * settings.PDF_COMPRESSION_DPI / 72)
                target_height = ceil(hypot(matrix.c, matrix.d) * settings.PDF_COMPRESSION_DPI / 72)
                _, previous_width, previous_height = candidates.get(xref, (page.number, 0, 0))
                candidates[xref] = (page.number, max(target_width, previous_width), max(target_height, previous_height))

    for xref, (page_number, width, height) in candidates.items():
        if xref not in protected:
            yield xref, page_number, width, height


@serialized_pdf
def optimize_pdf_for_storage(original: bytes, settings: Settings) -> bytes:
    """Return a smaller PDF or the original bytes if optimization is unsuitable."""
    if not settings.PDF_COMPRESSION_ENABLED:
        return original
    try:
        with fitz.open(stream=original, filetype="pdf") as document:
            if document.is_encrypted or document.get_sigflags() > 0:
                return original
            if document.page_count > settings.PDF_COMPRESSION_MAX_PAGES:
                return original

            for xref, page_number, width, height in _scan_images(document, settings):
                original_image = document.extract_image(xref)
                if not original_image or original_image["cs-name"] not in {"DeviceRGB", "DeviceGray"}:
                    continue
                with Image.open(BytesIO(original_image["image"])) as image:
                    if image.mode not in {"L", "RGB", "1"} or "transparency" in image.info:
                        continue
                    if image.mode == "RGB" and settings.PDF_GRAYSCALE_ENABLED and _is_neutral(image):
                        image = image.convert("L")
                    elif image.mode == "1":
                        image = image.convert("L")
                    image.thumbnail((width, height), Image.Resampling.LANCZOS)
                    encoded = BytesIO()
                    image.save(encoded, format="JPEG", quality=settings.PDF_COMPRESSION_JPEG_QUALITY, subsampling=0)
                    if encoded.tell() < len(original_image["image"]):
                        document[page_number].replace_image(xref, stream=encoded.getvalue())

            optimized = document.tobytes(garbage=3, deflate=True, use_objstms=1)
            return optimized if len(optimized) < len(original) else original
    except Exception as exc:
        logger.warning("PDF storage optimization skipped ({})", type(exc).__name__)
        return original
