from datetime import datetime, timezone
from typing import Optional, List
from beanie import Document
from pydantic import Field

class DocumentChunk(Document):
    user_id: str
    document_id: str
    document_type: str  # e.g., 'policy', 'prescription'
    chunk_index: int
    page_number: Optional[int] = None
    text: str
    embedding: List[float]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "document_chunks"
