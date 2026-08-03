"""
Upload routes — migrated from uploadController.js.
"""

import math
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from app.middleware.auth import get_current_user
from app.middleware.upload_validation import validate_upload
from app.services.storage.file_upload_service import FileUploadService
from app.utils.file_validators import detect_mime_from_magic_bytes
from app.core.config import get_settings
from app.schemas.upload import UploadResponse
from app.models.stored_file import StoredFile

router = APIRouter(prefix="/upload", tags=["Upload"])


@router.post("", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = Depends(validate_upload),
    documentType: str = Form("claim-document"),
    current_user: dict = Depends(get_current_user)
):
    """Upload a file to GridFS."""
    settings = get_settings()
    
    # Read file buffer
    buffer = await file.read()
    
    # Validate size
    if len(buffer) > settings.max_file_size_bytes:
        raise HTTPException(status_code=400, detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB")
        
    # Validate magic bytes
    actual_mime = detect_mime_from_magic_bytes(buffer)
    if not actual_mime:
        raise HTTPException(status_code=400, detail="Could not determine true file type. Possible spoofing.")
        
    if actual_mime not in settings.allowed_mime_types_list:
         raise HTTPException(status_code=400, detail="File content does not match allowed types.")
         
    # Upload
    result = await FileUploadService.upload_file(
        buffer=buffer,
        original_filename=file.filename,
        mime_type=actual_mime,
        document_type=documentType,
        user_id=current_user["id"]
    )
    
    return UploadResponse(
        message="File uploaded successfully",
        fileId=result["storedFileId"],
        filename=result["storedFilename"],
        originalName=file.filename,
        contentType=actual_mime,
        size=len(buffer)
    )

@router.get("/{file_id}")
async def stream_file(file_id: str, current_user: dict = Depends(get_current_user)):
    """Stream a file from GridFS."""
    from bson import ObjectId
    
    try:
        stored_file = await StoredFile.get(ObjectId(file_id))
        if not stored_file or stored_file.is_deleted or stored_file.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="File not found")
            
        stream_generator = await FileUploadService.get_file_stream(stored_file.storage_key)
        return StreamingResponse(stream_generator, media_type=stored_file.mime_type)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File content not found in storage")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{file_id}")
async def delete_file(file_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a file from GridFS (rollback support)."""
    try:
        await FileUploadService.delete_file(file_id, current_user["id"])
        return {"success": True, "message": "File deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
