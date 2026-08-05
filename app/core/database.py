"""
MongoDB connection management using Motor (async driver) and Beanie (async ODM).
Also provides GridFS bucket access.
"""

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from beanie import init_beanie
from app.core.config import get_settings
from app.core.logging import logger

# Module-level references
_client: AsyncIOMotorClient | None = None
_atlas_client: AsyncIOMotorClient | None = None
_gridfs_bucket: AsyncIOMotorGridFSBucket | None = None


async def connect_to_mongodb() -> None:
    """Initialize MongoDB connection, Beanie ODM, and GridFS bucket."""
    global _client, _atlas_client, _gridfs_bucket

    settings = get_settings()

    logger.info("Connecting to MongoDB...")
    client_kwargs = {}
    if "mongodb+srv" in settings.MONGODB_URI or "tls=true" in settings.MONGODB_URI.lower():
        client_kwargs["tlsCAFile"] = certifi.where()
    _client = AsyncIOMotorClient(settings.MONGODB_URI, **client_kwargs)

    # Get the database from the URI (defaults to 'claimsupport')
    db = _client.get_default_database()
    if db is None:
        db = _client["claimsupport"]

    # Import all document models for Beanie initialization
    from app.models.user import User
    from app.models.policy import Policy
    from app.models.prescription import Prescription
    from app.models.analysis_report import AnalysisReport
    from app.models.activity_log import ActivityLog
    from app.models.counter import Counter
    from app.models.stored_file import StoredFile
    from app.models.document_chunk import DocumentChunk

    await init_beanie(
        database=db,
        document_models=[
            User,
            Policy,
            Prescription,
            AnalysisReport,
            ActivityLog,
            Counter,
            StoredFile,
        ],
    )

    # Initialize GridFS bucket
    _gridfs_bucket = AsyncIOMotorGridFSBucket(db, bucket_name="fs")

    # Connect to Atlas for Vector Search if configured
    if settings.MONGO_ATLAS_URI:
        logger.info("Connecting to MongoDB Atlas for Vector Search...")
        atlas_kwargs = {}
        if "mongodb+srv" in settings.MONGO_ATLAS_URI or "tls=true" in settings.MONGO_ATLAS_URI.lower():
            atlas_kwargs["tlsCAFile"] = certifi.where()
        _atlas_client = AsyncIOMotorClient(settings.MONGO_ATLAS_URI, **atlas_kwargs)
        atlas_db = _atlas_client.get_default_database("claim_support")
        await init_beanie(
            database=atlas_db,
            document_models=[DocumentChunk],
        )
        logger.info("MongoDB Atlas connected successfully")
    else:
        # Fallback to local DB if Atlas URI is not provided (vector search won't work)
        await init_beanie(
            database=db,
            document_models=[DocumentChunk],
        )

    logger.info("MongoDB connected successfully")


async def close_mongodb_connection() -> None:
    """Close the MongoDB connection."""
    global _client, _atlas_client, _gridfs_bucket

    if _client:
        _client.close()
        _client = None
        _gridfs_bucket = None
        logger.info("MongoDB connection closed")
        
    if _atlas_client:
        _atlas_client.close()
        _atlas_client = None
        logger.info("MongoDB Atlas connection closed")


def get_database():
    """Get the Motor database instance."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected. Call connect_to_mongodb() first.")
    db = _client.get_default_database()
    if db is None:
        db = _client["claimsupport"]
    return db


def get_gridfs_bucket() -> AsyncIOMotorGridFSBucket:
    """Get the GridFS bucket instance."""
    if _gridfs_bucket is None:
        raise RuntimeError("GridFS bucket is not initialized. Call connect_to_mongodb() first.")
    return _gridfs_bucket


def get_client() -> AsyncIOMotorClient:
    """Get the Motor client instance."""
    if _client is None:
        raise RuntimeError("MongoDB is not connected. Call connect_to_mongodb() first.")
    return _client
