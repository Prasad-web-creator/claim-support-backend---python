"""
Upload response schemas.
"""

from pydantic import BaseModel
from typing import Optional


class UploadResponse(BaseModel):
    message: str
    fileId: Optional[str] = None
    filename: Optional[str] = None
    originalName: Optional[str] = None
    contentType: Optional[str] = None
    size: Optional[int] = None
