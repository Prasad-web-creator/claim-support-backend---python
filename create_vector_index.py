import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load from .env directly since we are running as a script
load_dotenv()

async def create_index():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        print("ERROR: MONGODB_URI not found in .env")
        return
        
    client = AsyncIOMotorClient(uri)
    db = client.get_default_database()
    
    print(f"Creating vector index on database: {db.name}")
    
    index_def = {
        "name": "vector_index",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": 768,
                    "similarity": "cosine"
                },
                {
                    "type": "filter",
                    "path": "user_id"
                },
                {
                    "type": "filter",
                    "path": "document_id"
                }
            ]
        }
    }
    
    try:
        # Atlas requires the 'createSearchIndexes' command
        result = await db.command({
            "createSearchIndexes": "document_chunks",
            "indexes": [index_def]
        })
        print(f"Index creation triggered successfully: {result}")
        print("Note: Atlas Search indexes take a few minutes to build. You can verify the status in the Atlas UI.")
    except Exception as e:
        print(f"Error creating index. It may already exist or there might be an issue: {e}")

if __name__ == "__main__":
    asyncio.run(create_index())
