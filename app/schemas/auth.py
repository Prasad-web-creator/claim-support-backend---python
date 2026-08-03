"""
Authentication request/response schemas.
"""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str
    email: str
    phone: str
    otp: str


class LoginRequest(BaseModel):
    phone: str
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
