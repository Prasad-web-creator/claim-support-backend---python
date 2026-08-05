import asyncio
from typing import Optional
from app.models.document_chunk import DocumentChunk
from app.services.rag.chunking import chunk_text
from app.services.rag.embedding import generate_embedding
from app.core.logging import logger

async def process_and_index_document(
    user_id: str,
    document_id: str,
    document_type: str,
    text: Optional[str]
):
    """
    Chunks a document's text, generates embeddings, and saves to MongoDB.
    Skips if chunks for this document already exist.
    """
    if not text or not text.strip():
        logger.info(f"[{document_type}] No text to index for {document_id}")
        return

    # Check if already indexed to prevent duplicates
    existing = await DocumentChunk.find_one(
        DocumentChunk.document_id == document_id,
        DocumentChunk.user_id == user_id
    )
    if existing:
        logger.info(f"[{document_type}] Document {document_id} already indexed. Skipping.")
        return

    logger.info(f"[{document_type}] Chunking text for {document_id}...")
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    
    if not chunks:
        return

    logger.info(f"[{document_type}] Generating embeddings for {len(chunks)} chunks...")
    
    # Process sequentially or in small batches to respect rate limits
    docs_to_insert = []
    for i, chunk in enumerate(chunks):
        try:
            embedding = await generate_embedding(chunk)
            doc_chunk = DocumentChunk(
                user_id=user_id,
                document_id=document_id,
                document_type=document_type,
                chunk_index=i,
                text=chunk,
                embedding=embedding
            )
            docs_to_insert.append(doc_chunk)
            # brief sleep to prevent rapid rate limiting if many chunks
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"[{document_type}] Failed to embed chunk {i} for {document_id}: {e}")

    if docs_to_insert:
        logger.info(f"[{document_type}] Saving {len(docs_to_insert)} chunks to database...")
        await DocumentChunk.insert_many(docs_to_insert)
        logger.info(f"[{document_type}] Indexing complete for {document_id}.")
