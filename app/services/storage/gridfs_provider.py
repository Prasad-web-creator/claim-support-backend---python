"""MongoDB GridFS storage access."""

from urllib.parse import urlencode

from bson import ObjectId
from gridfs.errors import NoFile

from app.core.database import get_database, get_gridfs_bucket
from app.services.storage.storage_provider import StorageProvider


class GridFSProvider(StorageProvider):
    async def upload_file(self, buffer: bytes, storage_key: str, metadata: dict | None = None) -> dict:
        file_id = await get_gridfs_bucket().upload_from_stream(
            storage_key, buffer, metadata=metadata or {},
        )
        return {"key": storage_key, "provider": "local", "fileId": str(file_id)}

    async def find_file(self, storage_key: str) -> dict | None:
        files = get_database()["fs.files"]
        if ObjectId.is_valid(storage_key):
            match = await files.find_one({"_id": ObjectId(storage_key)})
            if match:
                return match
        return await files.find_one({"filename": storage_key}, sort=[("uploadDate", -1)])

    async def download_file(self, storage_key: str):
        record = await self.find_file(storage_key)
        if not record:
            raise FileNotFoundError("File content not found")
        try:
            stream = await get_gridfs_bucket().open_download_stream(record["_id"])
        except NoFile as exc:
            raise FileNotFoundError("File content not found") from exc

        async def file_iterator():
            try:
                while chunk := await stream.readchunk():
                    yield chunk
            finally:
                stream.close()

        return file_iterator()

    async def delete_file(self, storage_key: str) -> bool:
        record = await self.find_file(storage_key)
        if not record:
            return False
        try:
            await get_gridfs_bucket().delete(record["_id"])
        except NoFile:
            return False
        return True

    async def generate_signed_url(self, storage_key: str, expires_in_seconds: int = 900) -> str:
        return f"/api/files/stream?{urlencode({'key': storage_key})}"
