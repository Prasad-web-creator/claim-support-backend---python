"""
CRUD routes factory — dynamic route generation for standard CRUD entities.
Replaces the individual controller files (policyController, prescriptionController).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel

from app.middleware.auth import get_current_user
from app.services.crud_service import CrudService

def create_crud_router(
    entity_name: str,
    model_class,
    schema_create: BaseModel,
    schema_update: BaseModel,
    searchable_fields: list[str] = None,
    prefix_override: str = None
) -> APIRouter:
    """Generate standard CRUD routes for a given model."""
    
    prefix = prefix_override if prefix_override else f"/{entity_name.lower()}s"
    router = APIRouter(prefix=prefix, tags=[entity_name])
    service = CrudService(model_class, entity_name, searchable_fields)
    
    def _serialize(result):
        d = result.dict(by_alias=True)
        if "_id" in d and d["_id"] is not None:
            d["_id"] = str(d["_id"])
        return d
    
    @router.post("")
    async def create_entity(
        request: Request,
        data: schema_create,
        current_user: dict = Depends(get_current_user)
    ):
        """Create a new entity."""
        result = await service.create(current_user["id"], data.dict(exclude_unset=True))
        return _serialize(result)

    @router.get("")
    async def list_entities(
        request: Request,
        page: int = Query(1, ge=1),
        limit: int = Query(10, ge=1, le=100),
        search: str = "",
        sortBy: str = "id",
        sortOrder: str = "desc",
        current_user: dict = Depends(get_current_user)
    ):
        """List entities with pagination and search."""
        result = await service.list_paginated(
            user_id=current_user["id"],
            page=page,
            limit=limit,
            search=search,
            sort_by=sortBy,
            sort_order=sortOrder
        )
        return result

    @router.get("/{entity_id}")
    async def get_entity(
        request: Request,
        entity_id: str,
        current_user: dict = Depends(get_current_user)
    ):
        """Get a single entity by ID."""
        result = await service.get_by_id(current_user["id"], entity_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"{entity_name} not found")
        return _serialize(result)

    @router.put("/{entity_id}")
    async def update_entity(
        request: Request,
        entity_id: str,
        data: schema_update,
        current_user: dict = Depends(get_current_user)
    ):
        """Update an entity."""
        result = await service.update(current_user["id"], entity_id, data.dict(exclude_unset=True))
        if not result:
             raise HTTPException(status_code=404, detail=f"{entity_name} not found")
        return _serialize(result)

    class BatchDeleteRequest(BaseModel):
        ids: list[str]

    @router.post("/batch-delete")
    async def batch_delete_entities(
        request: Request,
        body: BatchDeleteRequest,
        current_user: dict = Depends(get_current_user)
    ):
        """Batch delete entities."""
        result = await service.delete_batch(current_user["id"], body.ids)
        deleted_count = result.get("deletedCount", result.get("deleted", 0))
        skipped_count = result.get("skippedCount", len(body.ids) - deleted_count)
        return {
            "success": True,
            "deletedCount": deleted_count,
            "skippedCount": skipped_count,
            "message": f"Successfully deleted {deleted_count} {entity_name.lower()}(s)"
        }

    @router.delete("/{entity_id}")
    async def delete_entity(
        request: Request,
        entity_id: str,
        current_user: dict = Depends(get_current_user)
    ):
        """Delete an entity."""
        success = await service.delete(current_user["id"], entity_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"{entity_name} not found")
        return {"success": True, "message": f"{entity_name} deleted successfully"}
        
    return router
