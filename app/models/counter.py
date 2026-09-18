"""
Counter document model — migrated from Counter.js (Mongoose).
Provides per-user, per-entity auto-incrementing sequence numbers.
"""

from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ReturnDocument


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
        """
        Atomically increment and return the next sequence number.

        Uses a single findAndModify with upsert so that concurrent analyses for
        the same user (multi-policy sessions) can never be handed the same
        sequence number.
        """
        doc = await cls.get_motor_collection().find_one_and_update(
            {"userId": user_id, "entityType": entity_type},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc.get("seq", 1)) if doc else 1
