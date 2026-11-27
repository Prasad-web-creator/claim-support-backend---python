import asyncio
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlparse
from uuid import uuid4

import fitz
import pytest
from beanie import init_beanie
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from PIL import Image, ImageDraw

from app.api.routes import upload
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError, app_error_handler
from app.models.counter import Counter
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.models.stored_file import StoredFile
from app.services import cleanup_service
from app.services.crud_service import CrudService
from app.services.storage import gridfs_provider, storage_monitor
from app.services.storage.file_upload_service import GRIDFS_STORAGE_SCHEMA, FileUploadService
from app.services.storage.pdf_optimization import optimize_pdf_for_storage
from app.services.ttl_service import retire_retention_indexes


def test_upload_enforces_runtime_size_for_declared_and_streamed_bodies(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1)
    app = FastAPI()
    app.include_router(upload.router)
    app.dependency_overrides[upload.get_current_user] = lambda: {"id": "u1"}

    async def store(**kwargs):
        return {"storedFileId": "opaque", "storedFilename": "opaque.png"}

    monkeypatch.setattr(FileUploadService, "upload_file", store)
    with TestClient(app) as client:
        signature = b"\x89PNG\r\n\x1a\n"
        exact_limit = signature + b"x" * (1024 * 1024 - len(signature))
        accepted = client.post("/upload", files={"file": ("test.png", exact_limit, "image/png")})
        assert accepted.status_code == 200 and accepted.json()["size"] == len(exact_limit)

        too_big = client.post("/upload", files={"file": ("test.png", exact_limit + b"x", "image/png")})
        assert too_big.status_code == 413
        assert "maximum size of 1 MB" in too_big.json()["detail"]

        def chunks():
            yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\n'
            yield b'Content-Type: image/png\r\n\r\n' + signature
            for _ in range(35):
                yield b"x" * (64 * 1024)
            yield b"\r\n--test--\r\n"

        streamed = client.post("/upload", headers={"Content-Type": "multipart/form-data; boundary=test"}, content=chunks())
        assert streamed.status_code == 413


def test_pdf_optimization_keeps_color_and_native_text():
    picture = Image.effect_noise((1600, 2000), 20).convert("RGB")
    ImageDraw.Draw(picture).rectangle((100, 100, 400, 400), fill=(255, 0, 0))
    image_bytes = BytesIO()
    picture.save(image_bytes, format="PNG")
    with fitz.open() as original:
        scanned = original.new_page(width=400, height=500)
        scanned.insert_image(scanned.rect, stream=image_bytes.getvalue())
        original.new_page().insert_text((40, 40), "Policy schedule: explicit treatment benefit.")
        source = original.tobytes(deflate=True)
    optimized = optimize_pdf_for_storage(source, get_settings())
    assert len(optimized) < len(source) * 0.55
    with fitz.open(stream=optimized, filetype="pdf") as transformed:
        assert transformed.page_count == 2
        assert "explicit treatment benefit" in transformed[1].get_text()
        scan = transformed[0].get_pixmap(colorspace=fitz.csRGB)
        red, green, blue = scan.pixel(45, 45)
        assert red > 200 and green < 35 and blue < 35
    assert optimize_pdf_for_storage(b"broken PDF", get_settings()) == b"broken PDF"


def test_retention_migration_only_removes_legacy_created_at_ttl():
    from mongomock import MongoClient

    db = MongoClient()["retention_tests"]
    db.policies.create_index("createdAt", expireAfterSeconds=30 * 86400)
    db.prescriptions.create_index("createdAt")
    db.otpcodes.create_index("expires_at", expireAfterSeconds=0)
    db.policies.insert_one({"policyName": "synthetic"})
    assert len(retire_retention_indexes(db)) == 1
    assert "createdAt_1" in db.policies.index_information()
    assert retire_retention_indexes(db, apply=True)[0]["outcome"] == "removed"
    assert retire_retention_indexes(db, apply=True) == []
    assert db.policies.count_documents({}) == 1
    assert "createdAt_1" in db.prescriptions.index_information()
    assert "expires_at_1" in db.otpcodes.index_information()


@pytest.mark.asyncio
async def test_real_gridfs_dedup_ownership_delete_and_orphan_cleanup(monkeypatch):
    uri = os.environ.get("CLAIMSUPPORT_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set CLAIMSUPPORT_TEST_MONGO_URI to run the isolated MongoDB integration test")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1", "::1", "mongo"}, "Use an isolated test server."
    db_name = f"claimsupport_test_{uuid4().hex[:12]}"
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    bucket = AsyncIOMotorGridFSBucket(db)
    await init_beanie(
        database=db, document_models=[StoredFile, Policy, Prescription, Counter],
    )
    for module in (gridfs_provider, cleanup_service):
        monkeypatch.setattr(module, "get_database", lambda: db)
        monkeypatch.setattr(module, "get_gridfs_bucket", lambda: bucket)
    monkeypatch.setattr(storage_monitor, "get_database", lambda: db)

    source = b"\x89PNG\r\n\x1a\nsynthetic fixture"
    service = FileUploadService()
    try:
        first, second, third = await asyncio.gather(*(
            service.upload_file(source, f"{owner}.png", "image/png", "policy", owner)
            for owner in ("first", "second", "first")
        ))
        handles = [entry["storedFileId"] for entry in (first, second, third)]
        assert len(set(handles)) == 3
        assert await db["fs.files"].count_documents({}) == 1
        assert await service.get_file_bytes(handles[0], "first") == source
        assert await service.get_file_bytes(handles[1], "second") == source
        with pytest.raises(NotFoundError):
            await service.get_file_bytes(handles[0], "second")
        api = FastAPI()
        api.add_exception_handler(AppError, app_error_handler)
        api.include_router(upload.router)
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as http:
            api.dependency_overrides[upload.get_current_user] = lambda: {"id": "first"}
            downloaded = await http.get(f"/upload/{handles[0]}")
            assert downloaded.content == source
            assert downloaded.headers["Cache-Control"] == "private, no-store"
            api.dependency_overrides[upload.get_current_user] = lambda: {"id": "second"}
            denied = await http.get(f"/upload/{handles[0]}")
            assert denied.status_code == 404
        policy_service = CrudService(Policy, "Policy")
        with pytest.raises(NotFoundError):
            await policy_service.create("second", {"gridFsFileId": handles[0]})
        policy = await policy_service.create("first", {"gridFsFileId": handles[0]})
        await service.delete_file(handles[0], "first")
        await service.delete_file(handles[2], "first")
        assert await service.get_file_bytes(handles[0], "first") == source
        assert await policy_service.delete("first", str(policy.id))
        assert await service.get_file_bytes(handles[1], "second") == source

        old = datetime.now(timezone.utc) - timedelta(days=2)
        marker = {"storageSchema": GRIDFS_STORAGE_SCHEMA}
        past_orphan = await bucket.upload_from_stream("test-unreferenced", b"orphan", metadata=marker)
        linked = await bucket.upload_from_stream("test-direct-reference", b"linked", metadata=marker)
        unverified_legacy = await bucket.upload_from_stream("test-legacy", b"legacy")
        fresh = await bucket.upload_from_stream("test-recent", b"recent", metadata=marker)
        await db["fs.files"].update_many(
            {"_id": {"$in": [past_orphan, linked, unverified_legacy]}}, {"$set": {"uploadDate": old}},
        )
        await db.policies.insert_one({"userId": "legacy", "gridFsFileId": str(linked)})
        monkeypatch.setattr(get_settings(), "DATA_CLEANUP_DRY_RUN", False)
        monkeypatch.setattr(get_settings(), "ORPHAN_CLEANUP_GRACE_HOURS", 12)
        result = await cleanup_service.run_cleanup()
        assert result["deleted"] == 1
        assert await db["fs.files"].find_one({"_id": past_orphan}) is None
        assert await db["fs.files"].find_one({"_id": linked})
        assert await db["fs.files"].find_one({"_id": unverified_legacy})
        assert await db["fs.files"].find_one({"_id": fresh})
        await service.delete_file(handles[1], "second")
        assert await db["fs.files"].count_documents({}) == 3
        usage = await storage_monitor.check_storage()
        assert usage["scope"] == "database" and usage["used_bytes"] > 0
    finally:
        await client.drop_database(db_name)
        client.close()
