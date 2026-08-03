"""
Local Disk Storage Provider — migrated from LocalStorageProvider.js.
Uses GridFS conceptually for DB-backed local storage, or raw disk if preferred.
Since the original app used GridFS, we implement GridFS here.
"""

from io import BytesIO
from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.database import get_gridfs_bucket
from app.services.storage.storage_provider import StorageProvider
from app.core.logging import logger


class GridFSProvider(StorageProvider):
    """Storage provider using MongoDB GridFS."""

    async def upload_file(self, buffer: bytes, storage_key: str, metadata: dict | None = None) -> dict:
        """Upload file bytes to GridFS."""
        bucket = get_gridfs_bucket()
        
        file_id = await bucket.upload_from_stream(
            storage_key,
            buffer,
            metadata=metadata or {}
        )
        
        logger.info(f"[GridFS] Uploaded {storage_key} with ID {file_id}")
        
        return {
            "key": storage_key,
            "provider": "local",
            "fileId": str(file_id)
        }

    async def download_file(self, storage_key: str):
        """Download a file from GridFS as an async generator."""
        bucket = get_gridfs_bucket()
        
        # Find the latest file by filename (storage_key)
        cursor = bucket.find({"filename": storage_key}).sort("uploadDate", -1).limit(1)
        files = await cursor.to_list(length=1)
        
        if not files:
            raise FileNotFoundError(f"File not found in GridFS: {storage_key}")
            
        file_id = files[0]["_id"]
        
        # FastAPI StreamingResponse expects an async generator
        async def file_iterator():
            stream = await bucket.open_download_stream(file_id)
            try:
                while True:
                    chunk = await stream.readchunk()
                    if not chunk:
                        break
                    yield chunk
            finally:
                pass # GridFS streams in motor close automatically or don't need explicit close
                
        return file_iterator()

    async def delete_file(self, storage_key: str) -> bool:
        """Delete all versions of a file from GridFS."""
        bucket = get_gridfs_bucket()
        
        cursor = bucket.find({"filename": storage_key})
        files = await cursor.to_list(length=None)
        
        for f in files:
            await bucket.delete(f["_id"])
            
        logger.info(f"[GridFS] Deleted {len(files)} file(s) for key {storage_key}")
        return len(files) > 0

    async def generate_signed_url(self, storage_key: str, expires_in_seconds: int = 900) -> str:
        """
        GridFS doesn't have native signed URLs like S3.
        We return a route-able URL to the local backend's file-serving endpoint.
        The endpoint will verify JWT auth.
        """
        settings = get_settings()
        # In a real setup with a domain, this would be the public URL.
        # For now, relative path that the frontend can append to backend URL.
        # Format: /api/files/stream?key=STORAGE_KEY
        params = urlencode({"key": storage_key})
        return f"/api/files/stream?{params}"
