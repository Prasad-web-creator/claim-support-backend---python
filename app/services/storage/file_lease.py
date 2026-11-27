"""Serialize GridFS metadata and reference changes across app instances."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core.exceptions import AppError
from app.core.logging import logger
from app.models.stored_file import StoredFile


_LEASE_DURATION = timedelta(minutes=5)
_WAIT_SECONDS = 30


@asynccontextmanager
async def hold_file_lease(identity: str):
    collection = StoredFile.get_motor_collection().database["file_content_leases"]
    token = uuid4().hex
    deadline = asyncio.get_running_loop().time() + _WAIT_SECONDS
    while True:
        now = datetime.now(timezone.utc)
        try:
            await collection.update_one(
                {"_id": identity, "expiresAt": {"$lte": now}},
                {"$set": {"owner": token, "expiresAt": now + _LEASE_DURATION}},
                upsert=True,
            )
            break
        except DuplicateKeyError:
            if asyncio.get_running_loop().time() >= deadline:
                raise AppError("File is being processed. Please try again shortly.", status_code=503)
            await asyncio.sleep(0.1)

    async def renew():
        now = datetime.now(timezone.utc)
        result = await collection.update_one(
            {"_id": identity, "owner": token, "expiresAt": {"$gt": now}},
            {"$set": {"expiresAt": now + _LEASE_DURATION}},
        )
        if not result.matched_count:
            raise AppError("File is being processed. Please try again shortly.", status_code=503)

    try:
        yield renew
    finally:
        try:
            await collection.delete_one({"_id": identity, "owner": token})
        except PyMongoError as exc:
            logger.warning("GridFS coordination lease will expire ({})", type(exc).__name__)
