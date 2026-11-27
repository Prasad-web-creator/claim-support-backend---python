"""Measure Atlas storage consumption independently of health requests."""

from pymongo.errors import OperationFailure

from app.core.config import get_settings
from app.core.database import get_database
from app.core.logging import logger


async def check_storage() -> dict[str, str | int | float]:
    settings = get_settings()
    database = get_database()
    try:
        usage = await database.command("atlasSize", 1)
        used_bytes = int(usage["atlasSize"])
        scope = "cluster"
    except (OperationFailure, KeyError):
        usage = await database.command("dbStats", 1, scale=1)
        used_bytes = int(usage["dataSize"]) + int(usage["indexSize"])
        scope = "database"

    percent = round(100 * used_bytes / settings.STORAGE_CAPACITY_BYTES, 2)
    status = "ok"
    if percent >= settings.STORAGE_CRITICAL_THRESHOLD_PERCENT:
        status = "critical"
    elif percent >= settings.STORAGE_WARNING_THRESHOLD_PERCENT:
        status = "warning"
    log = {"ok": logger.info, "warning": logger.warning, "critical": logger.error}[status]
    log("MongoDB storage usage scope={} bytes={} percent={} status={}", scope, used_bytes, percent, status)
    return {"scope": scope, "used_bytes": used_bytes, "percent": percent, "status": status}
