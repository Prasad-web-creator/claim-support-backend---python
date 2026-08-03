"""
Analysis request/response schemas.
"""

from pydantic import BaseModel
from typing import Optional


class AnalysisStartRequest(BaseModel):
    prescriptionPath: str
    policyPath: str
