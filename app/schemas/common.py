"""
Common/shared schemas.
"""

from pydantic import BaseModel
from typing import Any, Optional


class ErrorResponse(BaseModel):
    success: bool = False
    message: str


class HealthResponse(BaseModel):
    status: str
    timestamp: str
