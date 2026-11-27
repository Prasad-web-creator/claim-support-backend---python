"""
Authentication request/response schemas.
"""

from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field

from app.core.language import Language


PHONE_PATTERN = r"^[6-9]\d{9}$"  # Indian 10-digit mobile number (new registrations)


class SendOtpRequest(BaseModel):
    channel: Literal["email"] = "email"
    purpose: Literal["login", "register"]
    email: EmailStr
    language: Language = "en"


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    phone: Optional[str] = Field(default=None, pattern=PHONE_PATTERN)
    otp: str = Field(min_length=1, max_length=32)
    channel: Literal["email"] = "email"


class LoginRequest(BaseModel):
    channel: Literal["email"] = "email"
    email: EmailStr
    otp: str = Field(min_length=1, max_length=32)


class GoogleSignInRequest(BaseModel):
    idToken: str = Field(min_length=1, max_length=4096)


class VerifyContactSendRequest(BaseModel):
    channel: Literal["email"] = "email"


class VerifyContactRequest(BaseModel):
    channel: Literal["email"] = "email"
    otp: str = Field(min_length=1, max_length=32)


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
    googleLinked: bool = False
    preferredLanguage: Language = "en"
