"""
File validators — magic byte detection, MIME type validation, structural validation.
Migrated from uploadValidation.js and advancedFileValidator.js.
"""

import fitz  # PyMuPDF

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx"}

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def detect_mime_from_magic_bytes(buffer: bytes) -> str | None:
    """Detect actual file type from magic bytes (anti-spoofing)."""
    if len(buffer) < 4:
        return None

    # PDF: %PDF
    if buffer[:5] == b"%PDF-":
        return "application/pdf"
    # PNG: 89 50 4E 47
    if buffer[0] == 0x89 and buffer[1] == 0x50 and buffer[2] == 0x4E and buffer[3] == 0x47:
        return "image/png"
    # JPEG: FF D8 FF
    if buffer[0] == 0xFF and buffer[1] == 0xD8 and buffer[2] == 0xFF:
        return "image/jpeg"
    # GIF: 47 49 46
    if buffer[0] == 0x47 and buffer[1] == 0x49 and buffer[2] == 0x46:
        return "image/gif"
    # DOCX/ZIP: PK\x03\x04
    if buffer[0] == 0x50 and buffer[1] == 0x4B and buffer[2] == 0x03 and buffer[3] == 0x04:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    return None


def validate_file_extension(filename: str) -> bool:
    """Check if the file extension is allowed."""
    import os

    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def validate_file_structure(file_path: str, mime_type: str) -> bool:
    """
    Perform deep structural validation of the file.
    Raises an error if the file is structurally invalid.
    """
    try:
        if mime_type == "application/pdf":
            doc = fitz.open(file_path)
            try:
                if doc.is_encrypted:
                    raise ValueError("PDF is encrypted or password protected")
                if doc.page_count == 0:
                    raise ValueError("PDF has no pages")
            finally:
                doc.close()

        elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            from docx import Document

            doc = Document(file_path)
            if not doc.paragraphs:
                raise ValueError("DOCX has no content")

        # Image validation is covered by magic bytes check
        return True

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Structural validation failed: {e}")
