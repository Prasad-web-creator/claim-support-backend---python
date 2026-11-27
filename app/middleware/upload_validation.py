"""
FastAPI Upload Middleware — replaces multer from Node.js.
Validates file extension, size, and mime type before passing to the route.
"""

from fastapi import UploadFile, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from app.core.config import get_settings
from app.utils.file_validators import validate_file_extension


def size_error() -> str:
    return f"File exceeds the maximum size of {get_settings().MAX_FILE_SIZE_MB} MB."


class LimitedUploadRoute(APIRoute):
    """Cap the multipart body during parsing, before loading file bytes."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        if "POST" not in self.methods:
            return handler

        async def handle(request: Request):
            settings = get_settings()
            body_limit = settings.max_file_size_bytes + 1024 * 1024
            content_length = request.headers.get("content-length", "")
            numeric_length = len(content_length) <= 16 and content_length.isascii() and content_length.isdecimal()
            if numeric_length and int(content_length) > body_limit:
                raise HTTPException(status_code=413, detail=size_error())
            if settings.REQUIRE_ANALYSIS_CONSENT:
                from app.middleware.auth import get_current_user
                from app.services.analysis_jobs import require_analysis_consent

                user = await get_current_user(request, credentials=None)
                await require_analysis_consent(
                    user["id"], request.headers.get("X-Analysis-Job-Id"), require_job=True
                )
            received = 0

            async def receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > body_limit:
                    # Starlette closes partially spooled files on this exception.
                    raise MultiPartException(size_error())
                return message

            capped = Request(request.scope, receive=receive)
            try:
                async with capped.form(max_files=1, max_fields=4, max_part_size=16 * 1024):
                    return await handler(capped)
            except StarletteHTTPException as exc:
                if received > body_limit:
                    raise HTTPException(status_code=413, detail=size_error()) from exc
                raise

        return handle


async def validate_upload(file: UploadFile) -> UploadFile:
    """
    Validates an uploaded file.
    Use as a FastAPI dependency for routes accepting files.
    """
    settings = get_settings()

    # Starlette records bytes written to the spool during multipart parsing.
    if file.size is not None and file.size > settings.max_file_size_bytes:
        raise HTTPException(status_code=413, detail=size_error())
    
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
