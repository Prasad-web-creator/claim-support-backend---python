"""
Authentication request/response schemas.
"""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field


PHONE_PATTERN = r"^[6-9]\d{9}$"  # Indian 10-digit mobile number (new registrations)
LEGACY_PHONE_PATTERN = r"^\d{1,10}$"  # older accounts were created with 1-10 digits


class SendOtpRequest(BaseModel):
    phone: str = Field(..., pattern=LEGACY_PHONE_PATTERN)
    purpose: str = Field(..., pattern=r"^(login|register)$")
    email: Optional[str] = None  # register only: lets us reject a taken email before sending an SMS


class RegisterRequest(BaseModel):
    name: str
    email: str
    phone: str = Field(..., pattern=PHONE_PATTERN)
    otp: str


class LoginRequest(BaseModel):
    phone: str = Field(..., pattern=LEGACY_PHONE_PATTERN)
    otp: str


class RefreshRequest(BaseModel):
    refreshToken: str


class TokenResponse(BaseModel):
    token: str
    refreshToken: str
    user: Optional[dict] = None


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
