"""Validate upload extensions and detect file types from magic bytes."""

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx"}
PRESCRIPTION_MIME_TYPES = {"application/pdf", "image/jpeg", "image/jpg", "image/png"}


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
