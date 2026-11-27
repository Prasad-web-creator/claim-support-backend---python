"""Read PDFs and scanned documents locally with bounded OCR resources."""

import asyncio
import csv
import io
import math
import os
import re
import subprocess
import time
from collections import defaultdict

import fitz
from PIL import Image, ImageOps

from app.core.config import Settings, get_settings
from app.core.exceptions import OCRError
from app.services.pdf.pdf_access import pdf_access, serialized_pdf


_document_worker = asyncio.Semaphore(1)
_MAX_SOURCE_IMAGE_PIXELS = 64_000_000
_MAX_DECODED_IMAGE_PIXELS = 24_000_000
_RESERVED_MARKER = re.compile(r"(?im)^(?=[ \t]*(?:---[ \t]*PAGE[ \t]+\d+[ \t]*---|\[OCR confidence:))")


class RecognizedText(str):
    def __new__(cls, text: str, confidence: float):
        result = super().__new__(cls, text)
        result.confidence = confidence
        return result


def _page_text(number: int, text: str, confidence: float | None = None) -> str:
    metadata = f"\n[OCR confidence: {confidence:.1f}%]" if confidence is not None else ""
    return f"--- PAGE {number} ---{metadata}\n{_RESERVED_MARKER.sub('> ', text)}"


def _prepare_image(image: Image.Image, settings: Settings) -> bytes:
    image = image.convert("L")
    if not settings.OCR_PREPROCESS_ENABLED:
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    import cv2
    import numpy as np

    cv2.setNumThreads(1)
    grayscale = np.asarray(image)
    cleaned = cv2.medianBlur(grayscale, 3)
    cleaned = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(cleaned)
    if settings.OCR_DESKEW_ENABLED:
        sample_scale = min(1, 1200 / max(cleaned.shape))
        sample = cv2.resize(cleaned, None, fx=sample_scale, fy=sample_scale)
        edges = cv2.Canny(sample, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 180, 50, minLineLength=50, maxLineGap=12)
        angles = []
        for x1, y1, x2, y2 in lines[:, 0] if lines is not None else ():
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            if -12 <= angle <= 12:
                angles.append(angle)
        if len(angles) >= 3:
            angle = float(np.median(angles))
            if abs(angle) >= 0.35:
                height, width = cleaned.shape
                rotation = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
                cleaned = cv2.warpAffine(cleaned, rotation, (width, height), borderValue=255)
    if settings.OCR_THRESHOLD_ENABLED:
        _, cleaned = cv2.threshold(cleaned, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    success, png = cv2.imencode(".png", cleaned)
    if not success:
        raise OCRError("Could not prepare the image for text recognition.")
    return png.tobytes()


def _run_tesseract(image: Image.Image, settings: Settings, deadline: float) -> str:
    payload = _prepare_image(image, settings)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OCRError("Text recognition timed out. Try a smaller document or enter the details manually.")
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TESSDATA_PREFIX") if key in os.environ}
    if settings.TESSDATA_PREFIX:
        environment["TESSDATA_PREFIX"] = settings.TESSDATA_PREFIX
    environment["OMP_THREAD_LIMIT"] = "1"
    try:
        result = subprocess.run(
            [settings.TESSERACT_CMD, "stdin", "stdout", "-l", settings.OCR_LANGUAGE,
             "--dpi", str(settings.OCR_DPI), "-c", "tessedit_create_tsv=1"],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            env=environment,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRError("Text recognition timed out. Try a smaller document or enter the details manually.") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OCRError("Text recognition is temporarily unavailable.") from exc

    lines: dict[tuple[str, ...], list[str]] = defaultdict(list)
    confidence: list[float] = []
    rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t")
    for row in rows:
        word = (row.get("text") or "").strip()
        if word and row.get("level") == "5":
            location = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
            lines[location].append(word)
            confidence.append(float(row["conf"]))
    text = "\n".join(" ".join(words) for words in lines.values())
    average = sum(confidence) / len(confidence) if confidence else 0
    if not confidence or average < settings.OCR_MIN_CONFIDENCE:
        raise OCRError("This scan could not be read reliably. Upload a clearer copy or enter the details manually.")
    return RecognizedText(text, average)


def _read_image(buffer: bytes, settings: Settings) -> str:
    deadline = time.monotonic() + settings.OCR_TIMEOUT_SECONDS
    with Image.open(io.BytesIO(buffer)) as image:
        resolution_error = "The image resolution is too large for text recognition. Upload a smaller image."
        pixels = image.width * image.height
        if pixels > _MAX_SOURCE_IMAGE_PIXELS:
            raise OCRError(resolution_error)
        if pixels > _MAX_DECODED_IMAGE_PIXELS:
            # Decode large camera JPEGs at half resolution to bound memory before resizing.
            image.draft(None, (image.width // 2, image.height // 2))
            pixels = image.width * image.height
            if pixels > _MAX_DECODED_IMAGE_PIXELS:
                raise OCRError(resolution_error)
        limit = settings.OCR_MAX_IMAGE_PIXELS
        if pixels > limit:
            scale = math.sqrt(limit / pixels)
            size = tuple(min(limit, max(1, int(length * scale))) for length in image.size)
            image.thumbnail(size, Image.Resampling.LANCZOS)
        result = _run_tesseract(ImageOps.exif_transpose(image), settings, deadline)
        return _page_text(1, result, getattr(result, "confidence", None))


def _has_readable_text(text: str) -> bool:
    return sum(char.isalnum() for char in text) >= 25 and len(text.split()) >= 5


def _needs_ocr(page: fitz.Page, text: str) -> bool:
    page_bounds = page.rect * page.derotation_matrix
    page_area = page_bounds.get_area()
    for image in page.get_image_info():
        bounds = fitz.Rect(image["bbox"]) & page_bounds
        if bounds.get_area() < page_area * 0.2:
            continue
        if not _has_readable_text(text) or not _has_readable_text(page.get_text("text", clip=bounds)):
            return True
    return False


@serialized_pdf
def _read_page(document: fitz.Document, page_num: int, settings: Settings) -> tuple[str, Image.Image | None]:
    page = document[page_num]
    text = page.get_text("text", sort=True).strip()
    if not _needs_ocr(page, text):
        return text, None
    zoom = min(
        settings.OCR_DPI / 72,
        math.sqrt(settings.OCR_MAX_IMAGE_PIXELS / max(1, page.rect.get_area())) * 0.98,
    )
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    return text, Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)


def _read_pdf(buffer: bytes, settings: Settings, max_pages: int | None) -> str:
    deadline = time.monotonic() + settings.OCR_TIMEOUT_SECONDS
    scan_count = 0
    limits = [limit for limit in (max_pages, settings.OCR_MAX_SCANNED_PAGES) if limit]
    scan_limit = min(limits) if limits else None
    text_parts = []
    with pdf_access:
        document = fitz.open(stream=buffer, filetype="pdf")
    try:
        with pdf_access:
            if document.needs_pass:
                raise OCRError("Unlock the PDF before uploading it.")
            page_count = document.page_count
        for page_num in range(page_count):
            if time.monotonic() >= deadline:
                raise OCRError("Text extraction timed out. Try a smaller document.")
            text, image = _read_page(document, page_num, settings)
            if image is None:
                if text:
                    text_parts.append(_page_text(page_num + 1, text))
                continue
            scan_count += 1
            if scan_limit is not None and scan_count > scan_limit:
                raise OCRError(f"This document has more than {scan_limit} scanned pages. Upload a shorter copy.")
            recognized = _run_tesseract(image, settings, deadline)
            native_lines = {" ".join(line.casefold().split()) for line in text.splitlines()}
            scanned_lines = [
                line for line in recognized.splitlines()
                if " ".join(line.casefold().split()) not in native_lines
            ]
            text_parts.append(_page_text(
                page_num + 1, "\n".join(([text] if text else []) + scanned_lines),
                getattr(recognized, "confidence", None),
            ))
    finally:
        with pdf_access:
            document.close()
    return "\n\n".join(text_parts)


async def _run_document(function, *args) -> str:
    try:
        await asyncio.wait_for(_document_worker.acquire(), timeout=get_settings().OCR_QUEUE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise OCRError("Document processing is busy. Please try again shortly.") from exc

    async def run():
        try:
            return await asyncio.to_thread(function, *args)
        finally:
            _document_worker.release()

    # The worker holds the slot even when the HTTP client disconnects.
    task = asyncio.create_task(run())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())
        raise


async def extract_text_from_image_bytes(
    image_bytes: bytes, mime_type: str = "image/png", doc_label: str = "Image"
) -> str:
    return await _run_document(_read_image, image_bytes, get_settings())


async def extract_text_from_pdf(pdf_buffer: bytes, max_pages: int | None = None) -> str:
    return await _run_document(_read_pdf, pdf_buffer, get_settings(), max_pages)
