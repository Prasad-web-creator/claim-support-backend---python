"""
Authentication request/response schemas.
"""

from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field


PHONE_PATTERN = r"^[6-9]\d{9}$"  # Indian 10-digit mobile number (new registrations)
LEGACY_PHONE_PATTERN = r"^\d{1,10}$"  # older accounts were created with 1-10 digits

# "sms" is the default so older app builds (which only knew SMS OTP) keep working.
Channel = Literal["sms", "email"]


class SendOtpRequest(BaseModel):
    channel: Channel = "sms"
    purpose: Literal["login", "register"]
    phone: Optional[str] = Field(default=None, pattern=LEGACY_PHONE_PATTERN)
    # login by email: the destination. register: lets us reject a taken email before sending.
    email: Optional[EmailStr] = None


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    phone: str = Field(..., pattern=PHONE_PATTERN)
    otp: str
    channel: Channel = "sms"  # which contact the OTP was sent to


class LoginRequest(BaseModel):
    channel: Channel = "sms"
    phone: Optional[str] = Field(default=None, pattern=LEGACY_PHONE_PATTERN)
    email: Optional[EmailStr] = None
    otp: str


class VerifyContactSendRequest(BaseModel):
    channel: Channel


class VerifyContactRequest(BaseModel):
    channel: Channel
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
    phone: Optional[str] = None
    emailVerified: bool = False
    phoneVerified: bool = False
