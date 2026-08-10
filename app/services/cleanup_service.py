import asyncio
import time
from datetime import datetime, timezone
from beanie import Document
from pymongo import ASCENDING
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from app.core.logging import logger
from app.core.config import get_settings
from app.core.database import get_database

async def run_cleanup():
    """
    Background job to clean up GridFS orphaned files and any legacy documents missed by TTL.
    Applies the mass-deletion threshold to abort if too many records are slated for deletion.
    """
    settings = get_settings()
    logger.info("[Cleanup Service] Starting daily background cleanup job")
    start_time = time.time()
    
    if settings.DATA_CLEANUP_DRY_RUN:
        logger.warning("[Cleanup Service] DRY RUN MODE ENABLED. No actual deletions will occur.")

    # Get the db name from URI or use default
    db = get_database()
    
    fs = AsyncIOMotorGridFSBucket(db, bucket_name="fs")
    
    # 1. Clean up GridFS Orphaned Files
    # An orphaned file is in `fs.files` but its `_id` is not referenced in any StoredFile.
    try:
        from app.models.stored_file import StoredFile
        
        # Get all file IDs from GridFS
        cursor = db.fs.files.find({}, {"_id": 1})
        gridfs_file_ids = [doc["_id"] async for doc in cursor]
        
        # Get all file IDs referenced by StoredFiles
        stored_files_cursor = StoredFile.find({}, projection_model=None).project({"_id": 1, "storageKey": 1})
        referenced_ids = []
        async for sf in stored_files_cursor:
            # Note: stored_file's storageKey is string representation of GridFS ObjectId
            if sf.storage_key:
                try:
                    from bson import ObjectId
                    referenced_ids.append(ObjectId(sf.storage_key))
                except Exception:
                    pass
                    
        # Find the difference
        orphaned_ids = set(gridfs_file_ids) - set(referenced_ids)
        total_orphaned = len(orphaned_ids)
        
        if total_orphaned > 0:
            logger.info(f"[Cleanup Service] Found {total_orphaned} orphaned GridFS files.")
            
            # Apply safety threshold: if more than 5% of all files are orphaned AND it exceeds 100 files, log a warning
            if len(gridfs_file_ids) > 0 and (total_orphaned / len(gridfs_file_ids)) > settings.DATA_CLEANUP_MAX_PERCENTAGE:
                if total_orphaned > settings.DATA_CLEANUP_MAX_COUNT:
                    logger.critical(f"[Cleanup Service] ABORTING GridFS cleanup: {total_orphaned} orphaned files exceeds absolute maximum of {settings.DATA_CLEANUP_MAX_COUNT}")
                    orphaned_ids = []
                else:
                    logger.warning(f"[Cleanup Service] GridFS orphaned files exceeds {settings.DATA_CLEANUP_MAX_PERCENTAGE*100}% of total files. Proceeding since it's under max count.")

            deleted_count = 0
            for file_id in orphaned_ids:
                if not settings.DATA_CLEANUP_DRY_RUN:
                    try:
                        await fs.delete(file_id)
                        deleted_count += 1
                    except Exception as e:
                        logger.error(f"[Cleanup Service] Failed to delete orphaned file {file_id}: {e}")
                else:
                    logger.info(f"[Cleanup Service] [DRY RUN] Would delete GridFS file: {file_id}")
                    deleted_count += 1
            
            logger.info(f"[Cleanup Service] GridFS cleanup complete. Deleted {deleted_count} files.")
        else:
            logger.info("[Cleanup Service] No orphaned GridFS files found.")
            
    except Exception as e:
        logger.error(f"[Cleanup Service] Error during GridFS cleanup: {e}")

    duration = time.time() - start_time
    logger.info(f"[Cleanup Service] Cleanup completed in {duration:.2f} seconds.")
    
    return {
        "duration": duration,
        "dry_run": settings.DATA_CLEANUP_DRY_RUN
    }
