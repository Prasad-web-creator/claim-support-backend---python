"""
Generic CRUD Service — replaces crudControllerFactory.js.
Handles generic create, read, update, delete operations for Models.
"""

from typing import TypeVar, Type, Any
from bson import ObjectId
from beanie import Document
from pydantic import BaseModel

from app.core.logging import logger
from app.models.activity_log import ActivityLog
from app.models.analysis_report import AnalysisReport
from app.models.stored_file import StoredFile

T = TypeVar("T", bound=Document)


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
        await doc.insert()
        
        # Link StoredFile if applicable
        file_id = data.get("gridFsFileId") or data.get("grid_fs_file_id")
        if file_id:
            from bson import ObjectId
            from app.models.stored_file import StoredFile
            try:
                sf = await StoredFile.get(ObjectId(file_id))
                if sf:
                    sf.document_id = str(doc.id)
                    await sf.save()
            except Exception:
                pass
        
        await ActivityLog(
            user_id=user_id,
            action=f"You uploaded the {self.entity_name.lower()}",
            entity_type=self.entity_name,
            entity_id=str(doc.id)
        ).insert()
        
        return doc

    async def get_by_id(self, user_id: str, doc_id: str) -> T | None:
        """Get a document by ID."""
        try:
            return await self.model.find_one(
                self.model.id == ObjectId(doc_id),
                getattr(self.model, "user_id") == user_id,
                getattr(self.model, "is_deleted") == False
            )
        except Exception:
            return None

    async def update(self, user_id: str, doc_id: str, data: dict) -> T | None:
        """Update a document by ID."""
        try:
            doc = await self.get_by_id(user_id, doc_id)
        except Exception:
            return None
            
        if not doc:
            return None
            
        update_data = {k: v for k, v in data.items() if hasattr(doc, k)}
        update_data["updated_by"] = user_id
        
        await doc.update({"$set": update_data})
        
        await ActivityLog(
            user_id=user_id,
            action=f"Updated {self.entity_name}",
            entity_type=self.entity_name,
            entity_id=str(doc.id)
        ).insert()
        
        # Fetch fresh doc
        return await self.get_by_id(user_id, doc_id)

    async def delete(self, user_id: str, doc_id: str) -> bool:
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
            
        await doc.delete()
        
        # Cascade Hard Deletes
        if self.entity_name in ["Policy", "Prescription"]:
            field_name = "policy_id" if self.entity_name == "Policy" else "prescription_id"
            # Delete associated reports
            await AnalysisReport.find(
                getattr(AnalysisReport, field_name) == str(doc.id),
                AnalysisReport.user_id == user_id
            ).delete()
            
            # Delete associated files from GridFS AND metadata
            from app.services.storage.file_upload_service import FileUploadService
            stored_files = await StoredFile.find(
                StoredFile.document_id == str(doc.id),
                StoredFile.user_id == user_id
            ).to_list()
            
            for sf in stored_files:
                try:
                    await FileUploadService.delete_file(str(sf.id), user_id)
                except Exception:
                    pass
            
        await ActivityLog(
            user_id=user_id,
            action=f"Permanently deleted {self.entity_name}",
            entity_type=self.entity_name,
            entity_id=str(doc.id)
        ).insert()
        
        return True

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
        query_conditions = [getattr(self.model, "user_id") == user_id, getattr(self.model, "is_deleted") == False]
        
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
        sort_direction = -1 if sort_order.lower() == "desc" else 1
        
        total = await self.model.find(*query_conditions).count()
        
        docs = await self.model.find(*query_conditions).sort(
            [(sort_by, sort_direction)]
        ).skip((page - 1) * limit).limit(limit).to_list()
        
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        
        def _serialize(d):
            dct = d.dict(by_alias=True)
            if "_id" in dct and dct["_id"] is not None:
                dct["_id"] = str(dct["_id"])
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
