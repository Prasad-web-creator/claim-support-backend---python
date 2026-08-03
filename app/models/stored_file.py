"""
StoredFile document model — migrated from StoredFile.js (Mongoose).
Stores metadata for ALL uploaded documents. S3 references removed per requirements.
"""

from datetime import datetime
from typing import Optional, Literal

from beanie import Document, Indexed
from pydantic import Field

DOCUMENT_TYPES = Literal[
    "policy",
    "prescription",
    "medical-report",
    "discharge-summary",
    "hospital-bill",
    "lab-report",
    "claim-document",
]

PROCESSING_STATUSES = Literal["pending", "processing", "completed", "failed"]
ANALYSIS_STATUSES = Literal["pending", "processing", "completed", "failed", "skipped"]


class StoredFile(Document):
    """File metadata document stored in MongoDB."""

    # ─── Ownership ────────────────────────────────────────────────────────────
    user_id: Indexed(str) = Field(alias="userId")  # type: ignore[valid-type]

    # ─── Document Reference ───────────────────────────────────────────────────
    document_id: Optional[Indexed(str)] = Field(default=None, alias="documentId")  # type: ignore[valid-type]
    document_type: str = Field(alias="documentType")

    # ─── File Identity ────────────────────────────────────────────────────────
    original_filename: str = Field(alias="originalFilename")
    stored_filename: str = Field(alias="storedFilename")
    storage_key: str = Field(alias="storageKey")
    storage_provider: str = Field(default="local", alias="storageProvider")  # 'local' only — S3 removed

    # ─── File Properties ──────────────────────────────────────────────────────
    mime_type: str = Field(alias="mimeType")
    file_size: int = Field(alias="fileSize")
    sha256_hash: str = Field(alias="sha256Hash")

    # ─── Processing State ─────────────────────────────────────────────────────
    processing_status: str = Field(default="pending", alias="processingStatus")
    analysis_status: str = Field(default="pending", alias="analysisStatus")
    document_version: int = Field(default=1, alias="documentVersion")
    processing_errors: list[str] = Field(default_factory=list, alias="processingErrors")

    # ─── Soft Delete ──────────────────────────────────────────────────────────
    is_deleted: bool = Field(default=False, alias="isDeleted")
    deleted_at: Optional[datetime] = Field(default=None, alias="deletedAt")
    deleted_by: Optional[str] = Field(default=None, alias="deletedBy")

    class Settings:
        name = "storedfiles"
        use_state_management = True

    class Config:
        populate_by_name = True
