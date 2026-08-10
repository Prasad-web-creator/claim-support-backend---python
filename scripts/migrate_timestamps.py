import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from app.core.database import connect_to_mongodb, close_mongodb_connection
from app.core.config import get_settings
from app.core.logging import logger

from app.models.activity_log import ActivityLog
from app.models.analysis_audit_log import AnalysisAuditLog
from app.models.analysis_report import AnalysisReport
from app.models.analysis_session import AnalysisSession
from app.models.policy import Policy
from app.models.prescription import Prescription
from app.models.stored_file import StoredFile


async def migrate():
    await connect_to_mongodb()
    settings = get_settings()

    models = [
        Policy, Prescription, AnalysisReport, AnalysisSession, 
        AnalysisAuditLog, ActivityLog, StoredFile
    ]

    is_dry_run = settings.DATA_CLEANUP_DRY_RUN
    logger.info(f"Starting Timestamp Migration. Dry Run: {is_dry_run}")

    for model in models:
        collection = model.get_motor_collection()
        collection_name = collection.name

        # Find documents where createdAt does not exist
        query = {"createdAt": {"$exists": False}}
        docs_to_migrate = await collection.count_documents(query)

        if docs_to_migrate == 0:
            logger.info(f"[{collection_name}] No missing timestamps found. Skipping.")
            continue

        logger.info(f"[{collection_name}] Found {docs_to_migrate} documents missing timestamps.")

        cursor = collection.find(query, {"_id": 1})
        migrated_count = 0

        async for doc in cursor:
            doc_id = doc["_id"]
            if hasattr(doc_id, "generation_time"):
                utc_time = doc_id.generation_time
                if not is_dry_run:
                    await collection.update_one(
                        {"_id": doc_id},
                        {"$set": {"createdAt": utc_time}}
                    )
                migrated_count += 1
                
        logger.info(f"[{collection_name}] Migrated {migrated_count} timestamps.")

    await close_mongodb_connection()
    logger.info("Migration Complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
