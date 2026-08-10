import asyncio
from typing import Type
from beanie import Document
from pymongo import IndexModel, ASCENDING
from app.core.logging import logger
from app.core.config import get_settings


async def ensure_ttl_indexes(models: list[Type[Document]]):
    """
    Ensure TTL indexes exist on the createdAt field of the provided models.
    If a TTL index exists but its expireAfterSeconds doesn't match the current config,
    it is dropped and recreated.
    """
    settings = get_settings()
    ttl_seconds = settings.ttl_seconds
    
    logger.info(f"[TTL Service] Ensuring TTL indexes are configured to {ttl_seconds} seconds ({settings.DATA_CLEANUP_DAYS} days)")

    for model in models:
        collection = model.get_motor_collection()
        collection_name = collection.name
        
        # We only want to manage collections in the allowlist
        if collection_name not in settings.collections_allowlist:
            logger.debug(f"[TTL Service] Skipping {collection_name} (not in allowlist)")
            continue

        try:
            indexes = await collection.index_information()
            
            # Find the existing TTL index on 'createdAt'
            ttl_index_name = None
            needs_recreate = False
            has_index = False
            
            for index_name, index_info in indexes.items():
                keys = index_info.get("key", [])
                if len(keys) == 1 and keys[0][0] == "createdAt":
                    has_index = True
                    existing_ttl = index_info.get("expireAfterSeconds")
                    if existing_ttl != ttl_seconds:
                        ttl_index_name = index_name
                        needs_recreate = True
                        break
                        
            if needs_recreate and ttl_index_name:
                logger.info(f"[TTL Service] Dropping outdated TTL index {ttl_index_name} on {collection_name}")
                await collection.drop_index(ttl_index_name)
                has_index = False

            if not has_index:
                logger.info(f"[TTL Service] Creating new TTL index on {collection_name} for createdAt ({ttl_seconds}s)")
                index = IndexModel([("createdAt", ASCENDING)], expireAfterSeconds=ttl_seconds)
                await collection.create_indexes([index])
                
        except Exception as e:
            logger.error(f"[TTL Service] Error managing TTL index for {collection_name}: {e}")
