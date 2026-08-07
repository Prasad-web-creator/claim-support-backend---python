"""
FastAPI Upload Middleware — replaces multer from Node.js.
Validates file extension, size, and mime type before passing to the route.
"""

from fastapi import UploadFile, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import get_settings
from app.utils.file_validators import validate_file_extension


async def validate_upload(file: UploadFile) -> UploadFile:
    """
    Validates an uploaded file.
    Use as a FastAPI dependency for routes accepting files.
    """
    settings = get_settings()

    # 1. Validate File Size
    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds maximum limit of {settings.MAX_FILE_SIZE_MB}MB"
        )
    await file.seek(0)
    
    # 2. Validate Extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing")
        
    if not validate_file_extension(file.filename):
        raise HTTPException(status_code=400, detail="Invalid file extension")

    # 3. Validate MIME Type Header
    if file.content_type not in settings.allowed_mime_types_list:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid file type. Allowed: {', '.join(settings.allowed_mime_types_list)}"
        )
        
    return file
