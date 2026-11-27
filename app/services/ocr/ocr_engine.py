"""Public document OCR entrypoints backed by local Tesseract and PyMuPDF."""

from app.services.ocr import local_ocr_engine


async def extract_text_from_image_bytes(
    image_bytes: bytes, mime_type: str = "image/png", doc_label: str = "Image",
) -> str:
    return await local_ocr_engine.extract_text_from_image_bytes(image_bytes, mime_type, doc_label)


async def extract_text_from_pdf(pdf_buffer: bytes, max_pages: int | None = None) -> str:
    return await local_ocr_engine.extract_text_from_pdf(pdf_buffer, max_pages=max_pages)
