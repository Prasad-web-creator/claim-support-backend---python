"""
Generic CRUD Service — replaces crudControllerFactory.js.
Handles generic create, read, update, delete operations for Models.
"""

from contextlib import nullcontext
from datetime import datetime, timezone
from typing import TypeVar, Type, Any
from beanie import Document, PydanticObjectId as ObjectId
from pydantic import BaseModel

from app.core.exceptions import ConflictError, ValidationError
from app.core.logging import logger
from app.models.analysis_report import AnalysisReport
from app.models.stored_file import StoredFile
from app.services.storage.file_upload_service import FileUploadService
from app.utils.file_validators import PRESCRIPTION_MIME_TYPES

T = TypeVar("T", bound=Document)

_PRESCRIPTION_SOURCE_FIELDS = {
    "gridFsFileId": ("grid_fs_file_id", ""),
    "isManual": ("is_manual", False),
    "extractedPrescriptionText": ("extracted_prescription_text", ""),
}


def _prescription_source_update(values: dict, stray_keys: dict) -> list[dict]:
    changes = {}
    for key, (legacy, default) in _PRESCRIPTION_SOURCE_FIELDS.items():
        if key in values:
            current = {"$ifNull": [f"${key}", f"${legacy}", default]}
            changes[key] = {"$ne": [current, {"$literal": values[key]}]}

    set_values = {key: {"$literal": value} for key, value in values.items()}
    any_change = {"$or": list(changes.values())}
    if "extractedPrescriptionJson" not in values:
        set_values["extractedPrescriptionJson"] = {"$cond": [any_change, {}, "$extractedPrescriptionJson"]}
    if "extractedPrescriptionText" not in values:
        set_values["extractedPrescriptionText"] = {"$cond": [any_change, "", "$extractedPrescriptionText"]}

    pipeline = [{"$set": set_values}]
    if stray_keys:
        pipeline.append({"$project": {key: 0 for key in stray_keys}})
    return pipeline


class CrudService:
    def __init__(self, model: Type[T], entity_name: str, searchable_fields: list[str] | None = None):
        self.model = model
        self.entity_name = entity_name
        self.searchable_fields = searchable_fields or []

    async def create(self, user_id: str, data: dict) -> T:
        """Create a new document."""
        # Handle agreement logic securely server-side
        if data.get("agreement") and data["agreement"].get("termsAccepted"):
            from datetime import datetime
            data["agreement"]["accepted"] = True
            data["agreement"]["acceptedAt"] = datetime.utcnow()

        doc = self.model(
            **data,
            user_id=user_id,
            created_by=user_id
        )
        file_id = data.get("gridFsFileId") or data.get("grid_fs_file_id")
        if self.entity_name == "Prescription":
            if file_id and data.get("isManual"):
                raise ValidationError("Choose one prescription input method.")
            if data.get("extractedPrescriptionText") and not data.get("isManual"):
                raise ValidationError("Only typed prescriptions can supply prescription text.")
        reference = FileUploadService.hold_owned_reference(file_id, user_id) if file_id else nullcontext()
        async with reference as stored_file:
            self._validate_prescription_file(stored_file)
            await doc.insert()
            if stored_file:
                await self._link_file(stored_file, doc)
        
        return doc

    async def get_by_id(self, user_id: str, doc_id: str) -> T | None:
        """Get a document by ID."""
        try:
            conditions = [
                self.model.id == ObjectId(doc_id),
                getattr(self.model, "user_id") == user_id,
            ]
            if hasattr(self.model, "is_deleted"):
                conditions.append(getattr(self.model, "is_deleted") == False)
            return await self.model.find_one(*conditions)
        except Exception:
            return None

    async def update(self, user_id: str, doc_id: str, data: dict) -> T | None:
        """Update a document by ID."""
        try:
            doc = await self.get_by_id(user_id, doc_id)
        except Exception:
            return None
        if doc is None:
            return None
            
        # A raw $set must use the key Mongo stores, which is the field's alias
        # (e.g. "inceptionDate"), never the Python name ("inception_date"): a value
        # written under the Python name is ignored when the document is read back.
        fields_dict = type(doc).model_fields
        by_alias = {(info.alias or name): name for name, info in fields_dict.items()}
        update_data = {}
        stray_keys = {}
        for k, v in data.items():
            field_name = k if k in fields_dict else by_alias.get(k)
            if not field_name:
                continue
            mongo_key = fields_dict[field_name].alias or field_name
            update_data[mongo_key] = v
            if mongo_key != field_name:
                stray_keys[field_name] = ""
        if "updated_by" in fields_dict:
            update_data[fields_dict["updated_by"].alias or "updated_by"] = user_id
        if "updated_at" in fields_dict:
            update_data[fields_dict["updated_at"].alias or "updated_at"] = datetime.now(timezone.utc)

        update_ops = {"$set": update_data}
        if stray_keys:
            update_ops["$unset"] = stray_keys
        file_id = update_data.get("gridFsFileId")
        effective_file = update_data.get("gridFsFileId", getattr(doc, "grid_fs_file_id", None))
        if self.entity_name == "Prescription":
            is_manual = update_data.get("isManual", doc.is_manual)
            if effective_file and is_manual:
                raise ValidationError("Choose one prescription input method.")
            if update_data.get("extractedPrescriptionText") and not is_manual:
                raise ValidationError("Only typed prescriptions can supply prescription text.")
        reference = FileUploadService.hold_owned_reference(file_id, user_id) if file_id else nullcontext()
        async with reference as stored_file:
            if file_id != getattr(doc, "grid_fs_file_id", None):
                self._validate_prescription_file(stored_file)
            if self.entity_name == "Prescription" and _PRESCRIPTION_SOURCE_FIELDS.keys() & update_data.keys():
                # Compare with the committed source and invalidate atomically.
                result = await self.model.get_motor_collection().update_one(
                    {"_id": doc.id, "userId": user_id}, _prescription_source_update(update_data, stray_keys),
                )
                if not result.matched_count:
                    return None
            elif self.entity_name == "Policy" and "gridFsFileId" in update_data and file_id != doc.grid_fs_file_id:
                cleared = {"extractedPolicyText": "", "extractedPolicyJson": {}}
                result = await self.model.get_motor_collection().update_one(
                    {"_id": doc.id, "userId": user_id, "gridFsFileId": doc.grid_fs_file_id or {"$in": ["", None]}},
                    {**update_ops, "$set": {**update_data, **cleared}},
                )
                if not result.matched_count:
                    raise ConflictError("The policy document changed. Reload the policy and try again.")
            else:
                await doc.update(update_ops)
            if stored_file:
                await self._link_file(stored_file, doc)
        
        # Fetch fresh doc
        return await self.get_by_id(user_id, doc_id)

    @staticmethod
    async def _link_file(stored_file: StoredFile, doc: T) -> None:
        await StoredFile.get_motor_collection().update_one(
            {"_id": stored_file.id}, {"$set": {"documentId": str(doc.id)}},
        )

    def _validate_prescription_file(self, stored_file: StoredFile | None) -> None:
        if self.entity_name == "Prescription" and stored_file and stored_file.mime_type not in PRESCRIPTION_MIME_TYPES:
            raise ValidationError("Upload a prescription as PDF, JPG or PNG.")

    async def delete(self, user_id: str, doc_id: str, *, older_than: datetime | None = None) -> bool:
        """Soft delete a document, or hard delete if no soft delete field."""
        try:
            doc = await self.model.find_one(
                self.model.id == ObjectId(doc_id),
                getattr(self.model, "user_id") == user_id
            )
        except Exception:
            return False
        if not doc:
            return False
            
        if older_than:
            result = await self.model.get_motor_collection().delete_one(
                {"_id": doc.id, "userId": user_id, "updatedAt": {"$lt": older_than}}
            )
            if not result.deleted_count:
                return False
        else:
            await doc.delete()
        
        # Cascade Hard Deletes
        if self.entity_name in ["Policy", "Prescription"]:
            # Note: We intentionally do NOT delete associated Analysis Reports 
            # so users can retain their historical analyses.
            
            # Delete associated files from GridFS AND metadata. delete_file keeps
            # any file another policy/prescription still uses (uploads are
            # de-duplicated by content, so one file can back several records).
            candidate_ids = set()
            for sf in await StoredFile.find(
                StoredFile.document_id == str(doc.id),
                StoredFile.user_id == user_id
            ).to_list():
                candidate_ids.add(str(sf.id))
            if getattr(doc, "grid_fs_file_id", None):
                candidate_ids.add(str(doc.grid_fs_file_id))

            for file_id in candidate_ids:
                try:
                    await FileUploadService.delete_file(file_id, user_id)
                except Exception:
                    pass
            
        return True

    async def delete_batch(self, user_id: str, doc_ids: list[str]) -> dict:
        """Hard delete multiple documents at once."""
        deleted_count = 0
        for doc_id in doc_ids:
            if await self.delete(user_id, doc_id):
                deleted_count += 1
                
        skipped_count = len(doc_ids) - deleted_count
        return {
            "requested": len(doc_ids),
            "deleted": deleted_count,
            "deletedCount": deleted_count,
            "skippedCount": skipped_count
        }

    async def list_paginated(
        self,
        user_id: str,
        page: int = 1,
        limit: int = 10,
        search: str = "",
        sort_by: str = "id",
        sort_order: str = "desc",
        **filters
    ) -> dict:
        """List documents with pagination and search."""
        query_conditions = [getattr(self.model, "user_id") == user_id]
        if hasattr(self.model, "is_deleted"):
            query_conditions.append(getattr(self.model, "is_deleted") == False)
        
        # Exact filters
        for k, v in filters.items():
            if v is not None and hasattr(self.model, k):
                query_conditions.append(getattr(self.model, k) == v)
                
        # Search functionality (using regex on searchable fields)
        if search and self.searchable_fields:
            import re
            regex = re.compile(re.escape(search), re.IGNORECASE)
            
            or_conditions = []
            for field in self.searchable_fields:
                if hasattr(self.model, field):
                    or_conditions.append(getattr(self.model, field) == regex)
                    
            if ObjectId.is_valid(search):
                or_conditions.append(self.model.id == ObjectId(search))
                
            if or_conditions:
                from beanie.operators import Or
                query_conditions.append(Or(*or_conditions))
                
        # Execute query
        actual_sort_by = "_id" if sort_by in ("id", "_id") else sort_by
        sort_direction = -1 if sort_order.lower() == "desc" else 1
        
        total = await self.model.find(*query_conditions).count()
        
        docs = await self.model.find(*query_conditions).sort(
            [(actual_sort_by, sort_direction)]
        ).skip((page - 1) * limit).limit(limit).to_list()
        
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        
        def _serialize(d):
            dct = d.dict(by_alias=True)
            if "_id" in dct and dct["_id"] is not None:
                dct["_id"] = str(dct["_id"])
            for k in ("createdAt", "updatedAt", "created_at", "updated_at"):
                v = dct.get(k)
                if isinstance(v, datetime):
                    if v.tzinfo is None:
                        dct[k] = v.isoformat() + "Z"
                    else:
                        dct[k] = v.isoformat()
                elif isinstance(v, str) and "T" in v and not v.endswith("Z") and "+" not in v:
                    dct[k] = v + "Z"
            if not dct.get("createdAt") and hasattr(d, "id") and d.id:
                try:
                    iso = d.id.generation_time.isoformat()
                    dct["createdAt"] = iso if (iso.endswith("Z") or "+" in iso) else iso + "Z"
                except Exception:
                    pass
            return dct

        return {
            "docs": [_serialize(doc) for doc in docs],
            "totalDocs": total,
            "limit": limit,
            "totalPages": total_pages,
            "page": page,
            "pagingCounter": (page - 1) * limit + 1,
            "hasPrevPage": page > 1,
            "hasNextPage": page < total_pages,
            "prevPage": page - 1 if page > 1 else None,
            "nextPage": page + 1 if page < total_pages else None
        }
