"""
Abstract Base Class for Policy RAG Retrieval Providers.
Allows swapping local semantic vector/BM25 retrieval with Gemini File Search,
Elasticsearch, Milvus, or Qdrant without altering application logic.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.models.rag_chunk import PolicyChunk


class BaseRetrievalProvider(ABC):
    """Abstract interface for all policy chunk retrieval providers."""

    @abstractmethod
    async def index_chunks(self, chunks: List[PolicyChunk]) -> int:
        """Indexes policy chunks into the storage/search backend."""
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        filters: Dict[str, Any],
        top_k: int = 5,
        as_of_date: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves top_k most relevant policy clauses matching the query
        while strictly obeying policy isolation and metadata filters.
        """
        pass

    @abstractmethod
    async def delete_policy_chunks(self, policy_id: str) -> int:
        """Deletes all chunks associated with a given policy_id."""
        pass
