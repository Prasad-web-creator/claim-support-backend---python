from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from app.core.config import settings
from app.core.security import create_access_token
from app.core.telemetry import _safe_event, _safe_transaction
from app.services.crud_service import CrudService
from app.services.ocr import local_ocr_engine
from main import app


@pytest.mark.parametrize("size", [(4000, 3000), (8000, 6000)])
def test_camera_jpeg_is_resized_and_oriented_before_ocr(size, monkeypatch):
    monkeypatch.setattr(settings, "OCR_MAX_IMAGE_PIXELS", 8_000_000)
    buffer = BytesIO()
    with Image.new("RGB", size, "white") as image:
        exif = image.getexif()
        exif[274] = 6  # Rotate the camera image 90 degrees clockwise.
        image.save(buffer, format="JPEG", exif=exif)

    def recognize(image, *_):
        assert image.width * image.height <= settings.OCR_MAX_IMAGE_PIXELS
        assert image.height / image.width == pytest.approx(4 / 3, rel=0.001)
        return "Synthetic prescription"

    monkeypatch.setattr(local_ocr_engine, "_run_tesseract", recognize)
    assert "Synthetic prescription" in local_ocr_engine._read_image(buffer.getvalue(), settings)


@pytest.mark.asyncio
async def test_document_upload_checks_consent_before_reading_body(monkeypatch):
    from app.services import analysis_jobs

    monkeypatch.setattr(settings, "REQUIRE_ANALYSIS_CONSENT", True)
    monkeypatch.setattr(analysis_jobs, "_required", lambda: True)

    async def incomplete_upload():
        pytest.fail("The upload body was read before consent verification")
        yield b""

    headers = {
        "Authorization": f"Bearer {create_access_token('test-owner')}",
        "Content-Type": "multipart/form-data; boundary=unused",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/upload", headers=headers, content=incomplete_upload())
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_typed_prescription_saves_without_document_consent(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_ANALYSIS_CONSENT", True)
    saved = {"_id": "manual-record", "isManual": True, "extractedPrescriptionText": "Fever"}
    create = AsyncMock(return_value=SimpleNamespace(dict=lambda **_: saved))
    monkeypatch.setattr(CrudService, "create", create)

    owner = {"Authorization": f"Bearer {create_access_token('test-owner')}"}
    typed = {"isManual": True, "extractedPrescriptionText": "Fever"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/prescriptions", headers=owner, json=typed)
        assert response.status_code == 200
        assert response.json() == saved

        for file_details in ({"gridFsFileId": "file"}, {"mimeType": "application/pdf", "fileSize": 12}):
            upload = await client.post("/api/prescriptions", headers=owner, json={**typed, **file_details})
            assert upload.status_code == 403

        policy = await client.post("/api/policies", headers=owner, json={"policyName": "Sample"})
        assert policy.status_code == 403

    create.assert_awaited_once_with("test-owner", typed)


def test_telemetry_discards_request_user_and_exception_details():
    private = {
        "request": {"data": "synthetic sensitive body", "headers": {"Authorization": "synthetic-token"}},
        "user": {"email": "synthetic@example.invalid"},
        "exception": {"values": [{"value": "sensitive exception"}]},
        "extra": {"otp": "synthetic-otp"},
        "breadcrumbs": {"values": ["sensitive breadcrumb"]},
    }
    assert _safe_event({"message": "unapproved"}, None) is None
    assert _safe_event({**private, "message": "ocr"}, None) == {"message": "ocr"}
    transaction = _safe_transaction({
        **private, "transaction": "ocr", "contexts": {"trace": {"op": "ocr", "data": private}},
    }, None)
    assert transaction == {"transaction": "ocr", "contexts": {"trace": {"op": "ocr"}}, "spans": []}
