"""Keep maintenance cadence across restarts and free-tier instance suspensions."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.database import get_database
from app.core.logging import logger
from app.services.cleanup_service import run_cleanup
from app.services.storage.storage_monitor import check_storage


async def _run_if_due(name, task, interval_hours: int) -> None:
    schedules = get_database()["maintenance_schedules"]
    now = datetime.now(timezone.utc)
    try:
        await schedules.update_one(
            {"_id": name},
            {"$setOnInsert": {"nextRunAt": now, "busyUntil": now}},
            upsert=True,
        )
    except DuplicateKeyError:
        pass
    claim = uuid4().hex
    acquired = await schedules.find_one_and_update(
        {"_id": name, "nextRunAt": {"$lte": now}, "busyUntil": {"$lte": now}},
        {"$set": {"busyUntil": now + timedelta(hours=1), "claim": claim}},
        return_document=ReturnDocument.AFTER,
    )
    if not acquired:
        return
    try:
        await task()
    except Exception as exc:
        logger.warning("Maintenance {} failed ({})", name, type(exc).__name__)
    else:
        await schedules.update_one(
            {"_id": name, "claim": claim},
            {"$set": {"nextRunAt": datetime.now(timezone.utc) + timedelta(hours=interval_hours)}},
        )
    finally:
        await schedules.update_one({"_id": name, "claim": claim}, {"$set": {"busyUntil": now}})


async def run_due_maintenance() -> None:
    settings = get_settings()
    if settings.ORPHAN_CLEANUP_ENABLED:
        await _run_if_due("gridfs_orphans", run_cleanup, settings.ORPHAN_CLEANUP_INTERVAL_HOURS)
    if settings.STORAGE_MONITOR_ENABLED:
        await _run_if_due("atlas_storage", check_storage, settings.STORAGE_MONITOR_INTERVAL_HOURS)
    if settings.DOCUMENT_RETENTION_DAYS and not settings.DATA_CLEANUP_DRY_RUN:
        await _run_if_due("document_retention", expire_documents, 24)


async def expire_documents() -> None:
    from app.models.policy import Policy
    from app.models.prescription import Prescription
    from app.services.analysis_jobs import LIVE
    from app.services.crud_service import CrudService

    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.DOCUMENT_RETENTION_DAYS)
    jobs = get_database()["analysis_jobs"]
    for model in (Policy, Prescription):
        service = CrudService(model, model.__name__)
        documents = model.get_motor_collection().find(
            {"updatedAt": {"$type": "date", "$lt": cutoff}},
            {"_id": 1, "userId": 1},
        ).limit(min(settings.DATA_CLEANUP_MAX_COUNT, 100))
        async for document in documents:
            owner = document.get("userId")
            if not owner:
                continue
            document_id = str(document["_id"])
            references = {"inputs.policyIds": document_id} if model is Policy else {
                "$or": [
                    {"inputs.prescriptionPath": document_id},
                    {"pendingPrescription": document_id},
                    {"preparedPrescription": document_id},
                ],
            }
            in_use = await jobs.find_one(
                {"userId": owner, "status": {"$in": LIVE}, "expiresAt": {"$gt": now}, **references},
                {"_id": 1},
            )
            if not in_use:
                await service.delete(owner, document_id, older_than=cutoff)
