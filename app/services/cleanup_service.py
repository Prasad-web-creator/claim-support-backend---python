"""Remove unreferenced GridFS objects after a configurable grace period."""

from datetime import datetime, timedelta, timezone
from math import floor

from gridfs.errors import NoFile

from app.core.config import get_settings
from app.core.database import get_database, get_gridfs_bucket
from app.core.logging import logger
from app.services.storage.file_lease import hold_file_lease
from app.services.storage.file_upload_service import GRIDFS_STORAGE_SCHEMA, file_lease_key, has_physical_reference


async def run_cleanup() -> dict[str, int | bool]:
    settings = get_settings()
    result = {"candidates": 0, "deleted": 0, "aborted": False, "dry_run": settings.DATA_CLEANUP_DRY_RUN}
    if not settings.ORPHAN_CLEANUP_ENABLED:
        return result

    files = get_database()["fs.files"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.ORPHAN_CLEANUP_GRACE_HOURS)
    total_files = await files.estimated_document_count()
    limit = min(
        settings.DATA_CLEANUP_MAX_COUNT,
        max(1, floor(total_files * settings.DATA_CLEANUP_MAX_PERCENTAGE)),
    )
    candidates = []
    # A legacy StoredFile row may already have expired while its policy still
    # points at the missing row. Those unknown mappings require reconciliation.
    async for record in files.find(
        {"uploadDate": {"$lt": cutoff}, "metadata.storageSchema": GRIDFS_STORAGE_SCHEMA},
        {"filename": 1, "metadata.sha256": 1},
    ):
        keys = [record["_id"], str(record["_id"]), record["filename"]]
        if await has_physical_reference(keys):
            continue
        candidates.append((record, keys))
        if len(candidates) > limit:
            logger.warning("GridFS orphan cleanup exceeded the safety limit ({})", limit)
            return {**result, "candidates": len(candidates), "aborted": True}

    result["candidates"] = len(candidates)
    if not settings.DATA_CLEANUP_DRY_RUN:
        for record, keys in candidates:
            digest = (record.get("metadata") or {}).get("sha256")
            async with hold_file_lease(file_lease_key(record["filename"], digest)) as renew:
                if not await has_physical_reference(keys):
                    await renew()
                    try:
                        await get_gridfs_bucket().delete(record["_id"])
                        result["deleted"] += 1
                    except NoFile:
                        continue
    logger.info("GridFS orphan cleanup completed {}", result)
    return result
