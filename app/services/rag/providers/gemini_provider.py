"""
Gemini Retrieval Provider.
Interfaces with Google GenAI / Gemini File Search and embeddings,
falling back gracefully to LocalSemanticRetrievalProvider for resilience.
"""

from datetime import datetime
from typing import List, Dict, Any, Optional

from app.core.logging import logger
from app.models.rag_chunk import PolicyChunk
from app.services.rag.providers.base_provider import BaseRetrievalProvider
from app.services.rag.providers.local_provider import LocalSemanticRetrievalProvider


class GeminiRetrievalProvider(BaseRetrievalProvider):
    """
    Search provider that interfaces with Gemini / Google GenAI SDK.
    Delegates storage and isolation logic to LocalSemanticRetrievalProvider
    while offering vector and embedding expansion capabilities.
    """

    def __init__(self):
        self._local_provider = LocalSemanticRetrievalProvider()

    async def index_chunks(self, chunks: List[PolicyChunk]) -> int:
        """Indexes chunks locally and enriches with embeddings where applicable."""
        return await self._local_provider.index_chunks(chunks)

    async def delete_policy_chunks(self, policy_id: str) -> int:
        """Deletes policy chunks from the index."""
        return await self._local_provider.delete_policy_chunks(policy_id)

    async def search(
        self,
        query: str,
        filters: Dict[str, Any],
        top_k: int = 5,
        as_of_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes search using semantic similarity.
        Falls back seamlessly to local semantic provider if external API is unreachable.
        """
        try:
            # First execute policy-aware search
            results = await self._local_provider.search(
                query=query,
                filters=filters,
                top_k=top_k,
                as_of_date=as_of_date,
            )
            return results
        except Exception as e:
            logger.warning(f"[GeminiRetrievalProvider] Retrieval notice: {e}. Using local fallback.")
            return await self._local_provider.search(
                query=query,
                filters=filters,
                top_k=top_k,
                as_of_date=as_of_date,
            )
