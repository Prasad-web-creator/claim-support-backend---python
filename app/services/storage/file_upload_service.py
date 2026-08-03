"""
File Upload Service — migrated from FileUploadService.js.
Coordinates the full upload workflow (hashing, UUID generation, storage, and DB metadata).
"""

import hashlib
import uuid
from pathlib import Path
from typing import Any

from app.core.logging import logger
from app.models.stored_file import StoredFile
from app.services.storage.gridfs_provider import GridFSProvider

# Singleton provider instance
_provider = GridFSProvider()


def _build_storage_key(document_type: str, user_id: str, document_id: str | None, stored_filename: str) -> str:
    """Builds the storage key following the bucket structure convention."""
    doc_id = document_id or "unlinked"
    return f"{document_type}s/{user_id}/{doc_id}/original/{stored_filename}"


class FileUploadService:
    """Upload Orchestrator."""

    @staticmethod
    async def upload_file(
        buffer: bytes,
        original_filename: str,
        mime_type: str,
        document_type: str,
        user_id: str,
        document_id: str | None = None
    ) -> dict[str, str]:
        """Upload a file and persist metadata to MongoDB."""
        
        # 1. Compute SHA256
        sha256_hash = hashlib.sha256(buffer).hexdigest()
        file_size = len(buffer)
        
        # 2. Duplicate Check
        existing_file = await StoredFile.find_one(
            StoredFile.user_id == user_id,
            StoredFile.sha256_hash == sha256_hash,
            StoredFile.is_deleted == False
        )
        
        if existing_file:
            # Verify it actually exists in GridFS
            try:
                # This will raise FileNotFoundError if missing
                bucket = get_gridfs_bucket()
                cursor = bucket.find({"filename": existing_file.storage_key}).limit(1)
                files = await cursor.to_list(length=1)
                if files:
                    logger.info(f"[FileUpload] Duplicate file detected for user {user_id}. Returning existing.")
                    return {
                        "storedFileId": str(existing_file.id),
                        "storageKey": existing_file.storage_key,
                        "storedFilename": existing_file.stored_filename
                    }
                else:
                    logger.warning(f"[FileUpload] Corrupted StoredFile {existing_file.id} found (missing in GridFS). Deleting metadata.")
                    await existing_file.delete()
            except Exception as e:
                logger.warning(f"[FileUpload] Error checking GridFS for duplicate: {e}")
            
        # 3. Generate UUID filename and storage key
        ext = Path(original_filename).suffix.lower() or ".pdf"
        unique_id = uuid.uuid4().hex[:8]
        stored_filename = f"{document_type}_{unique_id}{ext}"
        storage_key = _build_storage_key(document_type, user_id, document_id, stored_filename)
        
        # 4. Upload to GridFS
        await _provider.upload_file(
            buffer=buffer,
            storage_key=storage_key,
            metadata={
                "contentType": mime_type,
                "sha256": sha256_hash,
                "originalName": original_filename
            }
        )
        
        # 5. Save metadata to MongoDB
        stored_file = StoredFile(
            user_id=user_id,
            document_id=document_id,
            document_type=document_type,
            original_filename=original_filename,
            stored_filename=stored_filename,
            storage_key=storage_key,
            storage_provider="local",
            mime_type=mime_type,
            file_size=file_size,
            sha256_hash=sha256_hash,
            processing_status="pending",
            analysis_status="pending"
        )
        
        await stored_file.insert()
        logger.info(f"[FileUpload] Successfully saved metadata for {stored_filename}")
        
        return {
            "storedFileId": str(stored_file.id),
            "storageKey": storage_key,
            "storedFilename": stored_filename
        }

    @staticmethod
    async def delete_file(stored_file_id: str, deleted_by_user_id: str) -> None:
        """Delete a file from storage and metadata."""
        from bson import ObjectId
        record = await StoredFile.get(ObjectId(stored_file_id))
        if not record:
            raise ValueError(f"StoredFile not found: {stored_file_id}")
            
        try:
            await _provider.delete_file(record.storage_key)
        except Exception as e:
            logger.warning(f"Failed to delete file from GridFS: {e}")
        
        await record.delete()

    @staticmethod
    async def generate_signed_url(stored_file_id: str) -> str:
        """Generate a temporary signed URL (or stream route) for download."""
        from bson import ObjectId
        record = await StoredFile.get(ObjectId(stored_file_id))
        if not record or record.is_deleted:
            raise ValueError(f"StoredFile not found or deleted: {stored_file_id}")
            
        return await _provider.generate_signed_url(record.storage_key)

    @staticmethod
    async def get_file_stream(storage_key: str):
        """Get an async generator for file streaming."""
        return await _provider.download_file(storage_key)
