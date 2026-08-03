"""
Counter document model — migrated from Counter.js (Mongoose).
Provides per-user, per-entity auto-incrementing sequence numbers.
"""

from typing import Optional

from beanie import Document
from pydantic import Field


class Counter(Document):
    """Auto-incrementing counter for sequence numbers."""

    user_id: str = Field(alias="userId")
    entity_type: str = Field(alias="entityType")  # e.g., 'Policy', 'Prescription', 'AnalysisReport'
    seq: int = Field(default=0)

    class Settings:
        name = "counters"
        use_state_management = True

    class Config:
        populate_by_name = True

    @classmethod
    async def get_next_sequence(cls, user_id: str, entity_type: str) -> int:
        """Atomically increment and return the next sequence number."""
        counter = await cls.find_one(
            cls.user_id == user_id,
            cls.entity_type == entity_type,
        )
        if counter is None:
            counter = cls(user_id=user_id, entity_type=entity_type, seq=1)
            await counter.insert()
            return 1
        else:
            counter.seq += 1
            await counter.save()
            return counter.seq
