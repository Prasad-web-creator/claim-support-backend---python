import asyncio
from app.services.llm.ai_client import get_ai_client

from google.genai import types

async def generate_embedding(text: str) -> list[float]:
    """Generates an embedding vector for the given text using Gemini."""
    client = get_ai_client()
    # Using the standard text embedding model from Gemini
    response = await client.aio.models.embed_content(
        model='gemini-embedding-2',
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    return response.embeddings[0].values

async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generates embeddings for a batch of texts concurrently."""
    tasks = [generate_embedding(text) for text in texts]
    return await asyncio.gather(*tasks)
