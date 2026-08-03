"""
Auth routes — migrated from authController.js.
Mock authentication for testing.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Body, status
from pydantic import BaseModel

from app.core.security import create_access_token, generate_refresh_token, get_refresh_token_expiry
from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest, RefreshRequest, TokenResponse, UserResponse
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest):
    """Register a new user (mock implementation)."""
    # Check if user exists
    existing = await User.find_one(User.email == request.email)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    # Create user
    user = User(
        name=request.name,
        email=request.email,
        phone=request.phone,
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
async def login(request: LoginRequest):
    """Login a user (mock implementation)."""
    # For mock login, we just find the user by phone or return a default user
    user = await User.find_one(User.phone == request.phone)
    if not user:
        # Auto-create mock user for easy testing if not found
        user = User(
            name="Mock User",
            email=f"{request.phone}@mock.com",
            phone=request.phone,
            created_by="system",
            refresh_token=generate_refresh_token(),
            refresh_token_expiry=get_refresh_token_expiry()
        )
        await user.insert()
    else:
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
