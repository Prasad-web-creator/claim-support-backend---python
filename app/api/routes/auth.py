"""
Auth routes — migrated from authController.js.
Phone + OTP authentication. The OTP is verified on the server (see app/services/otp_service.py);
OTP_PROVIDER decides whether a real SMS is sent.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.security import create_access_token, generate_refresh_token, get_refresh_token_expiry
from app.models.user import User
from app.schemas.auth import (
    RegisterRequest, LoginRequest, RefreshRequest, SendOtpRequest, TokenResponse, UserResponse,
)
from app.middleware.auth import get_current_user
from app.middleware.rate_limiter import limiter
from app.services.otp_service import send_otp, verify_otp

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/send-otp")
@limiter.limit("5/minute")
async def send_otp_route(request: Request, body: SendOtpRequest):
    """Send an OTP to the phone number for login or registration."""
    user = await User.find_one(User.phone == body.phone)
    if body.purpose == "login":
        if not user:
            raise HTTPException(status_code=404, detail="User not found. Please register first.")
    else:
        if user:
            raise HTTPException(status_code=400, detail="This mobile number is already registered. Please login.")
        if body.email and await User.find_one(User.email == body.email):
            raise HTTPException(status_code=400, detail="User already exists")

    await send_otp(body.phone, body.purpose)
    return {"success": True, "message": "OTP sent"}


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(request: Request, body: RegisterRequest):

    # Check if user exists (before consuming the OTP)
    existing = await User.find_one(User.email == body.email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    if await User.find_one(User.phone == body.phone):
        raise HTTPException(status_code=400, detail="This mobile number is already registered. Please login.")

    await verify_otp(body.phone, "register", body.otp)

    # Create user
    user = User(
        name=body.name,
        email=body.email,
        phone=body.phone,
        created_by="system",
        refresh_token=generate_refresh_token(),
        refresh_token_expiry=get_refresh_token_expiry()
    )
    await user.insert()
    
    # Generate token
    token = create_access_token(str(user.id))
    
    return TokenResponse(
        token=token,
        refreshToken=user.refresh_token,
        user={"id": str(user.id), "name": user.name, "email": user.email}
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest):
    """Login a user with phone number + OTP."""
    user = await User.find_one(User.phone == body.phone)
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")

    await verify_otp(body.phone, "login", body.otp)

    # Update refresh token
    user.refresh_token = generate_refresh_token()
    user.refresh_token_expiry = get_refresh_token_expiry()
    await user.save()
        
    token = create_access_token(str(user.id))
    
    return TokenResponse(
        token=token,
        refreshToken=user.refresh_token,
        user={"id": str(user.id), "name": user.name, "email": user.email}
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """Refresh the access token using a valid refresh token."""
    user = await User.find_one(User.refresh_token == request.refreshToken)
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
        
    # Check if expired
    if user.refresh_token_expiry:
        # Ensure timezone-aware comparison
        expiry = user.refresh_token_expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Refresh token expired")
            
    # Generate new tokens
    user.refresh_token = generate_refresh_token()
    user.refresh_token_expiry = get_refresh_token_expiry()
    await user.save()
    
    token = create_access_token(str(user.id))
    
    return TokenResponse(
        token=token,
        refreshToken=user.refresh_token,
        user={"id": str(user.id), "name": user.name, "email": user.email}
    )

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user details."""
    user = await User.get(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email
    )

class UpdateProfileRequest(BaseModel):
    name: str
    email: str

@router.put("/me", response_model=UserResponse)
async def update_me(request: UpdateProfileRequest, current_user: dict = Depends(get_current_user)):
    """Update current user details."""
    user = await User.get(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if request.email != user.email:
        existing = await User.find_one(User.email == request.email)
        if existing:
            raise HTTPException(status_code=400, detail="Email address is already in use by another account")
        
    user.name = request.name
    user.email = request.email
    await user.save()
    
    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email
    )


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout current user."""
    user = await User.get(current_user["id"])
    if user:
        user.refresh_token = None
        user.refresh_token_expiry = None
        await user.save()
    return {"success": True, "message": "Logged out successfully"}
