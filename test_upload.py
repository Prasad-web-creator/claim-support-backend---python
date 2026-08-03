import asyncio
from app.core.database import connect_to_mongodb
from app.services.storage.gridfs_provider import GridFSProvider

async def test():
    await connect_to_mongodb()
    p = GridFSProvider()
    try:
        await p.upload_file(b'%PDF-1.4', 'test.pdf')
        print('success')
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test())
