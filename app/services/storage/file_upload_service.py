"""Manage owned upload records and shared GridFS content."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from beanie import PydanticObjectId as ObjectId
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.logging import logger
from app.models.stored_file import StoredFile
from app.services.storage.file_lease import hold_file_lease
from app.services.storage.gridfs_provider import GridFSProvider
from app.services.storage.pdf_optimization import optimize_pdf_for_storage

_provider = GridFSProvider()
_pdf_workers = asyncio.Semaphore(1)
GRIDFS_STORAGE_SCHEMA = 2
_ANALYSIS_COLLECTIONS = ("analysissessions", "analysisreports", "multipolicyanalysissessions")


def _receipt(record: StoredFile) -> dict[str, str]:
    return {
        "storedFileId": str(record.id),
        "storageKey": record.storage_key,
        "storedFilename": record.stored_filename,
    }


def file_lease_key(storage_key: str, digest: str | None) -> str:
    return f"sha256:{digest}" if digest else f"file:{storage_key}"


async def has_physical_reference(storage_keys: list[str | ObjectId]) -> bool:
    collection = StoredFile.get_motor_collection()
    keys = list(set(storage_keys))
    query = {"storageKey": {"$in": keys}}
    if await collection.find_one(query, {"_id": 1}):
        return True

    # Old imports may point at a GridFS ID directly instead of through StoredFile.
    db = collection.database
    query = {"$or": [{"gridFsFileId": {"$in": keys}}, {"grid_fs_file_id": {"$in": keys}}]}
    for name in ("policies", "prescriptions"):
        if await db[name].find_one(query, {"_id": 1}):
            return True
    for name in _ANALYSIS_COLLECTIONS:
        if await db[name].find_one(_analysis_reference_filter({"$in": keys}), {"_id": 1}):
            return True
    return False


def _analysis_reference_filter(file_reference) -> dict:
    return {"$or": [
        {"policyId": file_reference},
        {"prescriptionId": file_reference},
        {"policyAnalyses.policyId": file_reference},
    ]}


class FileUploadService:
    @staticmethod
    async def upload_file(
        buffer: bytes,
        original_filename: str,
        mime_type: str,
        document_type: str,
        user_id: str,
        document_id: str | None = None,
    ) -> dict[str, str]:
        settings = get_settings()
        digest = hashlib.sha256(buffer).hexdigest()
        filename = f"{uuid4().hex}{Path(original_filename).suffix.lower()}"
        storage_key = f"documents/{uuid4().hex}"
        lock_digest = digest if settings.FILE_DEDUPLICATION_ENABLED else None

        async with hold_file_lease(file_lease_key(storage_key, lock_digest)) as renew:
            stored_size = len(buffer)
            existing = None
            if settings.FILE_DEDUPLICATION_ENABLED:
                candidates = StoredFile.find(
                    {"sha256Hash": digest, "isDeleted": {"$ne": True}},
                ).sort("createdAt")
                async for candidate in candidates:
                    if await _provider.find_file(candidate.storage_key):
                        existing = candidate
                        break

            if existing:
                storage_key = existing.storage_key
                stored_size = existing.stored_size or existing.file_size
            else:
                if mime_type == "application/pdf":
                    async with _pdf_workers:
                        stored = await run_in_threadpool(optimize_pdf_for_storage, buffer, settings)
                else:
                    stored = buffer
                stored_size = len(stored)
                await renew()
                await _provider.upload_file(
                    buffer=stored,
                    storage_key=storage_key,
                    metadata={"contentType": mime_type, "sha256": digest, "storageSchema": GRIDFS_STORAGE_SCHEMA},
                )

            record = StoredFile(
                user_id=user_id,
                document_id=document_id,
                document_type=document_type,
                original_filename=original_filename,
                stored_filename=filename,
                storage_key=storage_key,
                storage_provider="local",
                mime_type=mime_type,
                file_size=len(buffer),
                stored_size=stored_size,
                sha256_hash=digest,
            )
            await renew()
            # Keep failed publication bytes for the orphan job. A network error
            # can leave the insert committed even though the caller saw failure.
            await record.insert()
            return _receipt(record)

    @staticmethod
    async def get_owned_file(stored_file_id: str, user_id: str) -> StoredFile:
        if not ObjectId.is_valid(stored_file_id):
            raise NotFoundError("File not found")
        record = await StoredFile.get(ObjectId(stored_file_id))
        if not record or record.user_id != user_id or record.is_deleted:
            raise NotFoundError("File not found")
        return record

    @staticmethod
    @asynccontextmanager
    async def hold_owned_reference(stored_file_id: str, user_id: str):
        record = await FileUploadService.get_owned_file(stored_file_id, user_id)
        async with hold_file_lease(file_lease_key(record.storage_key, record.sha256_hash)) as renew:
            record = await FileUploadService.get_owned_file(stored_file_id, user_id)
            await renew()
            yield record

    @staticmethod
    async def find_file_user(stored_file_id: str, user_id: str):
        """Find an owned policy or prescription that still links to the handle."""
        from app.models.policy import Policy
        from app.models.prescription import Prescription

        query = {
            "userId": user_id,
            "$or": [{"gridFsFileId": stored_file_id}, {"grid_fs_file_id": stored_file_id}],
        }
        return await Policy.find_one(query) or await Prescription.find_one(query)

    @staticmethod
    async def delete_file(stored_file_id: str, deleted_by_user_id: str) -> None:
        async with FileUploadService.hold_owned_reference(stored_file_id, deleted_by_user_id) as record:
            linked = await FileUploadService.find_file_user(stored_file_id, deleted_by_user_id)
            if linked is not None:
                if record.document_id != str(linked.id):
                    await StoredFile.get_motor_collection().update_one(
                        {"_id": record.id}, {"$set": {"documentId": str(linked.id)}},
                    )
                return

            # Some historical sessions/reports may store an upload ID directly.
            db = StoredFile.get_motor_collection().database
            if ObjectId.is_valid(record.document_id):
                associated = {"userId": deleted_by_user_id, "_id": ObjectId(record.document_id)}
                for name in ("policies", "prescriptions"):
                    if await db[name].find_one(associated, {"_id": 1}):
                        return
            query = {
                "userId": deleted_by_user_id,
                **_analysis_reference_filter(stored_file_id),
            }
            for name in _ANALYSIS_COLLECTIONS:
                if await db[name].find_one(query, {"_id": 1}):
                    return

            physical = await _provider.find_file(record.storage_key)
            keys = [record.storage_key]
            if physical:
                keys.extend((physical["_id"], str(physical["_id"]), physical["filename"]))
            await record.delete()
            if not await has_physical_reference(keys):
                try:
                    await _provider.delete_file(str(physical["_id"]) if physical else record.storage_key)
                except Exception as exc:
                    logger.warning("GridFS deletion will be retried by orphan cleanup ({})", type(exc).__name__)

    @staticmethod
    async def generate_signed_url(stored_file_id: str, user_id: str) -> str:
        record = await FileUploadService.get_owned_file(stored_file_id, user_id)
        return f"/api/upload/{record.id}"

    @staticmethod
    async def get_file_stream(storage_key: str):
        return await _provider.download_file(storage_key)

    @staticmethod
    async def get_file_bytes(stored_file_id: str, user_id: str) -> bytes:
        record = await FileUploadService.get_owned_file(stored_file_id, user_id)
        stream = await FileUploadService.get_file_stream(record.storage_key)
        return b"".join([chunk async for chunk in stream])
