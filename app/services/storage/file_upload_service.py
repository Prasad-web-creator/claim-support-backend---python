"""
File Upload Service — migrated from FileUploadService.js.
Coordinates the full upload workflow (hashing, UUID generation, storage, and DB metadata).
"""

import hashlib
import uuid
from pathlib import Path
from typing import Any

from beanie import PydanticObjectId as ObjectId
from app.core.database import get_gridfs_bucket
from app.core.logging import logger, log_section
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
    ) -> dict[str, Any]:
        """
        Coordinates hashing, deduplication, storage upload, and DB metadata persistence.
        """
        log_section("File Upload")
        # 1. Generate SHA256
        sha256_hash = hashlib.sha256(buffer).hexdigest()
        file_size = len(buffer)
        
        # 2. Check for duplicate within the same user's scope
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
                    logger.info(f"[File Upload] Duplicate document detected for user {user_id}. Returning existing file.")
                    return {
                        "storedFileId": str(existing_file.id),
                        "storageKey": existing_file.storage_key,
                        "storedFilename": existing_file.stored_filename
                    }
                else:
                    logger.warning(f"[File Upload] Corrupted file entry {existing_file.id} found (missing in GridFS). Deleting metadata.")
                    await existing_file.delete()
            except Exception as e:
                logger.warning(f"[File Upload] Error checking storage for duplicate: {e}")
            
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
        logger.info(f"[File Upload] Saved {stored_filename} ({file_size / 1024.0:.1f} KB, {mime_type})")
        
        return {
            "storedFileId": str(stored_file.id),
            "storageKey": storage_key,
            "storedFilename": stored_filename
        }

    @staticmethod
    async def find_file_user(stored_file_id: str, user_id: str):
        """
        A policy or prescription that still points at this stored file, if any.

        Uploads are de-duplicated by content hash, so one stored file can back
        several records (the same PDF uploaded twice, or the same file used as
        both a policy and a prescription).
        """
        from app.models.policy import Policy
        from app.models.prescription import Prescription

        return await Policy.find_one(
            Policy.user_id == user_id,
            Policy.grid_fs_file_id == stored_file_id,
        ) or await Prescription.find_one(
            Prescription.user_id == user_id,
            Prescription.grid_fs_file_id == stored_file_id,
        )

    @staticmethod
    async def delete_file(stored_file_id: str, deleted_by_user_id: str) -> None:
        """
        Delete a file from storage and metadata — unless another policy or
        prescription still uses it. Deleting a shared file would leave that
        record pointing at nothing, and its next analysis fails with
        "File metadata not found".
        """
        record = await StoredFile.get(ObjectId(stored_file_id))
        if not record:
            raise ValueError(f"StoredFile not found: {stored_file_id}")
        if record.user_id != deleted_by_user_id:
            raise ValueError(f"StoredFile not found: {stored_file_id}")

        user = await FileUploadService.find_file_user(stored_file_id, deleted_by_user_id)
        if user is not None:
            logger.info(
                f"[File Delete] Kept shared file {stored_file_id}: still used by record {user.id}"
            )
            if record.document_id != str(user.id):
                record.document_id = str(user.id)
                await record.save()
            return

        try:
            await _provider.delete_file(record.storage_key)
        except Exception as e:
            logger.warning(f"Failed to delete file from GridFS: {e}")
        
        await record.delete()

    @staticmethod
    async def generate_signed_url(stored_file_id: str) -> str:
        """Generate a temporary signed URL (or stream route) for download."""
        record = await StoredFile.get(ObjectId(stored_file_id))
        if not record or record.is_deleted:
            raise ValueError(f"StoredFile not found or deleted: {stored_file_id}")
            
        return await _provider.generate_signed_url(record.storage_key)

    @staticmethod
    async def get_file_stream(storage_key: str):
        """Get an async generator for file streaming."""
        return await _provider.download_file(storage_key)

    @staticmethod
    async def get_file_bytes(storage_key_or_id: str) -> bytes:
        """Get full file bytes from GridFS by storage key or StoredFile id."""
        storage_key = storage_key_or_id
        try:
            from beanie import PydanticObjectId
            if PydanticObjectId.is_valid(storage_key_or_id):
                sf = await StoredFile.get(PydanticObjectId(storage_key_or_id))
                if sf:
                    storage_key = sf.storage_key
        except Exception:
            pass

        try:
            stream = await FileUploadService.get_file_stream(storage_key)
            chunks = []
            async for chunk in stream:
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception:
            # Fallback: check if storage_key_or_id is directly a GridFS ObjectId
            try:
                from bson import ObjectId
                bucket = get_gridfs_bucket()
                stream = await bucket.open_download_stream(ObjectId(storage_key_or_id))
                chunks = []
                while True:
                    chunk = await stream.readchunk()
                    if not chunk:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)
            except Exception:
                raise

