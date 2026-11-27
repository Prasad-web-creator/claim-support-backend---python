"""
Deleting a policy must never remove a stored file another record still uses.

Uploads are de-duplicated by content hash, so the same PDF uploaded as a
prescription and as a policy (or as two policies) shares ONE stored file.
Removing one of those records used to delete the shared file, and the next
analysis of the other record failed with "File metadata not found for ID ...".
"""


import pytest
import pytest_asyncio

from app.services.storage import file_upload_service as fus

USER = "user-1"


@pytest_asyncio.fixture
async def store(monkeypatch):
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.models.counter import Counter
    from app.models.policy import Policy
    from app.models.prescription import Prescription
    from app.models.stored_file import StoredFile

    client = AsyncMongoMockClient()
    await init_beanie(
        database=client["shared_file_test"],
        document_models=[Policy, Prescription, StoredFile, Counter],
    )

    deleted_keys = []

    class FakeProvider:
        async def find_file(self, storage_key):
            return None

        async def delete_file(self, storage_key):
            deleted_keys.append(storage_key)
            return True

    monkeypatch.setattr(fus, "_provider", FakeProvider())

    async def make_file(key):
        sf = StoredFile(
            user_id=USER,
            document_type="document",
            original_filename=f"{key}.pdf",
            stored_filename=f"{key}.pdf",
            storage_key=key,
            storage_provider="local",
            mime_type="application/pdf",
            file_size=1,
            sha256_hash=key,
        )
        await sf.insert()
        return sf

    return {
        "Policy": Policy,
        "Prescription": Prescription,
        "StoredFile": StoredFile,
        "make_file": make_file,
        "deleted_keys": deleted_keys,
    }


async def _policy_service():
    from app.models.policy import Policy
    from app.services.crud_service import CrudService

    return CrudService(Policy, "Policy")


@pytest.mark.asyncio
async def test_removing_policy_keeps_file_shared_with_prescription(store):
    Policy, Prescription, StoredFile = store["Policy"], store["Prescription"], store["StoredFile"]

    shared = await store["make_file"]("shared")
    rx = Prescription(user_id=USER, grid_fs_file_id=str(shared.id))
    await rx.insert()

    # Policy 2 is the same PDF → dedup hands it the same stored file, and the
    # file is re-linked to the newest record.
    p2 = Policy(user_id=USER, policy_name="Policy 2", grid_fs_file_id=str(shared.id))
    await p2.insert()
    shared.document_id = str(p2.id)
    await shared.save()

    service = await _policy_service()
    assert await service.delete(USER, str(p2.id))

    # The prescription's file survives and now belongs to the prescription.
    kept = await StoredFile.get(shared.id)
    assert kept is not None
    assert kept.document_id == str(rx.id)
    assert store["deleted_keys"] == []


@pytest.mark.asyncio
async def test_removing_policy_deletes_its_own_unshared_file(store):
    Policy, StoredFile = store["Policy"], store["StoredFile"]

    own = await store["make_file"]("own")
    p = Policy(user_id=USER, policy_name="Policy 1", grid_fs_file_id=str(own.id))
    await p.insert()
    own.document_id = str(p.id)
    await own.save()

    service = await _policy_service()
    assert await service.delete(USER, str(p.id))

    assert await StoredFile.get(own.id) is None
    assert store["deleted_keys"] == ["own"]


@pytest.mark.asyncio
async def test_upload_rollback_keeps_file_already_used_by_another_record(store):
    Prescription, StoredFile = store["Prescription"], store["StoredFile"]

    shared = await store["make_file"]("shared")
    rx = Prescription(user_id=USER, grid_fs_file_id=str(shared.id))
    await rx.insert()

    # DELETE /upload/{id} after a failed policy save, on a deduplicated file.
    await fus.FileUploadService.delete_file(str(shared.id), USER)

    assert await StoredFile.get(shared.id) is not None
    assert store["deleted_keys"] == []


@pytest.mark.asyncio
async def test_prescription_update_invalidates_extraction_only_on_source_changes(store):
    from app.core.exceptions import ValidationError
    from app.services.crud_service import CrudService

    Prescription = store["Prescription"]
    first = await store["make_file"]("previous")
    second = await store["make_file"]("replacement")
    rx = Prescription(
        user_id=USER,
        grid_fs_file_id=str(first.id),
        extracted_prescription_text="Diagnosis: original",
        extracted_prescription_json={"diagnosis": "original"},
    )
    await rx.insert()
    service = CrudService(Prescription, "Prescription")
    unchanged = await service.update(USER, str(rx.id), {"gridFsFileId": str(first.id), "hospitalName": "Test"})
    assert unchanged.extracted_prescription_json == {"diagnosis": "original"}

    replaced = await service.update(USER, str(rx.id), {"gridFsFileId": str(second.id)})
    assert replaced.grid_fs_file_id == str(second.id)
    assert replaced.extracted_prescription_json == {}
    assert replaced.extracted_prescription_text == ""

    await Prescription.get_motor_collection().update_one(
        {"_id": rx.id},
        {"$set": {
            "extractedPrescriptionJson": {"diagnosis": "stale"},
        }},
    )
    with pytest.raises(ValidationError, match="Only typed prescriptions"):
        await service.update(USER, str(rx.id), {"extractedPrescriptionText": "$literal text"})

    typed = await Prescription(
        user_id=USER,
        is_manual=True,
        extracted_prescription_text="Diagnosis: original",
        extracted_prescription_json={"diagnosis": "original"},
    ).insert()
    updated = await service.update(USER, str(typed.id), {"extractedPrescriptionText": "$literal text"})
    assert updated.extracted_prescription_text == "$literal text"
    assert updated.extracted_prescription_json == {}
