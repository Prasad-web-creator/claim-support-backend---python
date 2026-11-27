"""Authenticated GridFS uploads and downloads."""

import asyncio

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.middleware.auth import get_current_user
from app.middleware.upload_validation import LimitedUploadRoute, size_error, validate_upload
from app.schemas.upload import UploadResponse
from app.services.pdf.pdf_extractor import is_scanned_pdf
from app.services.storage.file_upload_service import FileUploadService
from app.utils.file_validators import PRESCRIPTION_MIME_TYPES, detect_mime_from_magic_bytes

router = APIRouter(prefix="/upload", tags=["Upload"], route_class=LimitedUploadRoute)
_upload_workers = asyncio.Semaphore(2)


@router.post("", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = Depends(validate_upload),
    documentType: str = Form("claim-document"),
    current_user: dict = Depends(get_current_user),
):
    """Validate and store a document."""
    settings = get_settings()
    async with _upload_workers:
        buffer = await file.read(settings.max_file_size_bytes + 1)
        if len(buffer) > settings.max_file_size_bytes:
            raise HTTPException(status_code=413, detail=size_error())
        actual_mime = detect_mime_from_magic_bytes(buffer)
        if actual_mime not in settings.allowed_mime_types_list:
            raise HTTPException(status_code=400, detail="File content does not match an allowed file type.")
        if documentType == "prescription" and actual_mime not in PRESCRIPTION_MIME_TYPES:
            raise HTTPException(status_code=422, detail="Upload a prescription as PDF, JPG or PNG.")

        is_image_based = actual_mime.startswith("image/")
        if actual_mime == "application/pdf":
            try:
                is_image_based = await run_in_threadpool(is_scanned_pdf, buffer)
            except (RuntimeError, ValueError):
                raise HTTPException(status_code=422, detail="This PDF could not be opened.") from None

        result = await FileUploadService.upload_file(
            buffer=buffer,
            original_filename=file.filename,
            mime_type=actual_mime,
            document_type=documentType,
            user_id=current_user["id"],
        )
    return UploadResponse(
        message="File uploaded successfully",
        fileId=result["storedFileId"],
        filename=result["storedFilename"],
        originalName=file.filename,
        contentType=actual_mime,
        size=len(buffer),
        isImageBased=is_image_based,
    )


@router.get("/{file_id}")
async def stream_file(file_id: str, current_user: dict = Depends(get_current_user)):
    stored_file = await FileUploadService.get_owned_file(file_id, current_user["id"])
    try:
        stream = await FileUploadService.get_file_stream(stored_file.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File content not found in storage") from exc
    return StreamingResponse(
        stream, media_type=stored_file.mime_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{file_id}")
async def delete_file(file_id: str, current_user: dict = Depends(get_current_user)):
    await FileUploadService.delete_file(file_id, current_user["id"])
    return {"success": True, "message": "File deleted"}
