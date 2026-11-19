"""
Auth routes — migrated from authController.js.

OTP authentication over two independent channels:
  sms   -> OTP sent to the user's mobile number
  email -> OTP sent to the user's email address
A user registers with name + email + phone and verifies ONE of them with an OTP. They can
then log in with any channel whose contact is verified; the other contact can be verified
later from the profile (/auth/me/verify/*). A contact that was never verified cannot be
used to log in, so a mistyped phone/email at registration does not open the account to
whoever owns it.

The OTP is generated and verified on the server (see app/services/otp_service.py);
OTP_PROVIDER / EMAIL_OTP_PROVIDER decide whether a real SMS / email is sent.
"""

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from pymongo.errors import DuplicateKeyError

from app.core.security import create_access_token, generate_refresh_token, get_refresh_token_expiry
from app.models.user import User
from app.schemas.auth import (
    RegisterRequest, LoginRequest, RefreshRequest, SendOtpRequest, TokenResponse, UserResponse,
    VerifyContactRequest, VerifyContactSendRequest,
)
from app.middleware.auth import get_current_user
from app.middleware.rate_limiter import limiter
from app.services.otp_service import EMAIL, SMS, OtpSent, normalize_email, send_otp, verify_otp

router = APIRouter(prefix="/auth", tags=["Auth"])

CONTACT_NAME = {SMS: "mobile number", EMAIL: "email address"}
OTHER_METHOD = {SMS: "Email OTP", EMAIL: "SMS OTP"}


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _find_by_email(email: str) -> Optional[User]:
    """Case-insensitive: older accounts may have stored the email with capitals."""
    return await User.find_one({"email": {"$regex": f"^{re.escape(normalize_email(email))}$", "$options": "i"}})


async def _find_by_contact(channel: str, target: str) -> Optional[User]:
    if channel == SMS:
        return await User.find_one(User.phone == target)
    return await _find_by_email(target)


def is_verified(user: User, channel: str) -> bool:
    if channel == SMS:
        # None = account registered before verification was tracked; those could only be
        # created (and logged into) through phone OTP, so their phone counts as verified.
        return bool(user.phone) and user.phone_verified is not False
    return bool(user.email_verified)


def _target(channel: str, phone: Optional[str], email: Optional[str]) -> str:
    if channel == SMS:
        if not phone:
            raise HTTPException(status_code=422, detail="Please enter your mobile number.")
        return phone
    if not email:
        raise HTTPException(status_code=422, detail="Please enter your email address.")
    return normalize_email(email)


def _user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "emailVerified": is_verified(user, EMAIL),
        "phoneVerified": is_verified(user, SMS),
    }


def _sent_payload(sent: OtpSent) -> dict:
    if sent.delivered:
        message = f"OTP sent to {sent.destination}"
    elif sent.test_code:
        # OTP_PROVIDER / EMAIL_OTP_PROVIDER = static: no SMS/email service is set up.
        message = f"OTP could not be sent to {sent.destination}. Please use test OTP {sent.test_code}."
    else:  # console mode, or a reviewer test number/email with its own fixed code
        message = f"OTP could not be sent to {sent.destination}. Please use the test OTP given to you."
    return {
        "success": True,
        "message": message,
        "delivered": sent.delivered,
        "testOtp": sent.test_code,
        "channel": sent.channel,
        "destination": sent.destination,
        "otpLength": sent.otp_length,
        "expiresInSeconds": sent.expires_in_seconds,
        "resendAfterSeconds": sent.resend_after_seconds,
    }


async def _issue_tokens(user: User) -> TokenResponse:
    user.refresh_token = generate_refresh_token()
    user.refresh_token_expiry = get_refresh_token_expiry()
    await user.save()
    return TokenResponse(
        token=create_access_token(str(user.id)),
        refreshToken=user.refresh_token,
        user=_user_payload(user),
    )


async def _login_user(channel: str, target: str) -> User:
    """The account for a login attempt, or a clear error if it cannot log in this way."""
    user = await _find_by_contact(channel, target)
    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"No account found with this {CONTACT_NAME[channel]}. Please register first.",
        )
    # Email OTP is the only login method. Entering the OTP sent to the inbox proves the
    # user owns it, and /login then marks the email verified, so accounts whose email was
    # never verified (e.g. made with the old SMS sign-up) are not locked out.
    if channel != EMAIL and not is_verified(user, channel):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This {CONTACT_NAME[channel]} is not verified yet. Please log in with "
                f"{OTHER_METHOD[channel]} and verify it from your profile."
            ),
        )
    return user


async def _check_not_registered(phone: Optional[str], email: Optional[str]) -> None:
    if email and await _find_by_email(email):
        raise HTTPException(status_code=400, detail="This email address is already registered. Please login.")
    if phone and await User.find_one(User.phone == phone):
        raise HTTPException(status_code=400, detail="This mobile number is already registered. Please login.")


# ─── Login / registration ─────────────────────────────────────────────────────


@router.post("/send-otp")
@limiter.limit("5/minute")
async def send_otp_route(request: Request, body: SendOtpRequest):
    """Send a login or registration OTP by SMS or email."""
    target = _target(body.channel, body.phone, body.email)
    if body.purpose == "login":
        await _login_user(body.channel, target)
    else:
        await _check_not_registered(body.phone, body.email)
    return _sent_payload(await send_otp(body.channel, target, body.purpose))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(request: Request, body: RegisterRequest):
    """Create an account once the OTP sent to the chosen contact (email or phone) is verified."""
    email = normalize_email(body.email)
    await _check_not_registered(body.phone, email)  # before consuming the OTP

    target = body.phone if body.channel == SMS else email
    await verify_otp(body.channel, target, "register", body.otp)

    user = User(
        name=body.name.strip(),
        email=email,
        phone=body.phone,
        email_verified=body.channel == EMAIL,
        phone_verified=body.channel == SMS,
        created_by="system",
    )
    try:
        await user.insert()
    except DuplicateKeyError:  # registered concurrently
        raise HTTPException(status_code=400, detail="This email address is already registered. Please login.")
    return await _issue_tokens(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest):
    """Log in with an OTP sent to a verified phone number or email address."""
    target = _target(body.channel, body.phone, body.email)
    user = await _login_user(body.channel, target)
    await verify_otp(body.channel, target, "login", body.otp)
    if body.channel == SMS:
        user.phone_verified = True  # records legacy (None) accounts as verified
    else:
        user.email_verified = True
    return await _issue_tokens(user)


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

    return await _issue_tokens(user)


# ─── Current user ─────────────────────────────────────────────────────────────


async def _current(current_user: dict) -> User:
    user = await User.get(current_user["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current user details."""
    return UserResponse(**_user_payload(await _current(current_user)))


class UpdateProfileRequest(BaseModel):
    name: str
    email: EmailStr


class ChangeEmailSendOtpRequest(BaseModel):
    newEmail: EmailStr


class ChangeEmailVerifyRequest(BaseModel):
    newEmail: EmailStr
    otp: str
    name: Optional[str] = None


@router.put("/me", response_model=UserResponse)
async def update_me(request: UpdateProfileRequest, current_user: dict = Depends(get_current_user)):
    """Update current user details. A changed email must be verified again before email login."""
    user = await _current(current_user)
    email = normalize_email(request.email)
    if email != normalize_email(user.email):
        existing = await _find_by_email(email)
        if existing and existing.id != user.id:
            raise HTTPException(status_code=400, detail="Email address is already in use by another account")
        user.email_verified = False
    user.name = request.name
    user.email = email
    await user.save()
    return UserResponse(**_user_payload(user))


@router.post("/me/change-email/send-otp")
@limiter.limit("5/minute")
async def send_change_email_otp(
    request: Request, body: ChangeEmailSendOtpRequest, current_user: dict = Depends(get_current_user)
):
    """Send an OTP to verify a new email address before updating profile."""
    user = await _current(current_user)
    new_email = normalize_email(body.newEmail)
    if not new_email:
        raise HTTPException(status_code=422, detail="Please enter a valid email address.")
    
    # Check if the email is already in use by another account
    existing = await _find_by_email(new_email)
    if existing and existing.id != user.id:
        raise HTTPException(status_code=400, detail="This email address is already in use by another account.")
    
    return _sent_payload(await send_otp(EMAIL, new_email, "change_email"))


@router.post("/me/change-email/verify", response_model=UserResponse)
@limiter.limit("10/minute")
async def verify_change_email(
    request: Request, body: ChangeEmailVerifyRequest, current_user: dict = Depends(get_current_user)
):
    """Verify OTP and update user's email address and name."""
    user = await _current(current_user)
    new_email = normalize_email(body.newEmail)
    
    # Final check before applying
    existing = await _find_by_email(new_email)
    if existing and existing.id != user.id:
        raise HTTPException(status_code=400, detail="This email address is already in use by another account.")
    
    await verify_otp(EMAIL, new_email, "change_email", body.otp)
    
    user.email = new_email
    user.email_verified = True
    if body.name and body.name.strip():
        user.name = body.name.strip()
    await user.save()
    return UserResponse(**_user_payload(user))


async def _verification_target(user: User, channel: str) -> str:
    target = user.phone if channel == SMS else normalize_email(user.email)
    if not target:
        raise HTTPException(status_code=400, detail=f"Please add a {CONTACT_NAME[channel]} to your profile first.")
    if is_verified(user, channel):
        raise HTTPException(status_code=400, detail=f"Your {CONTACT_NAME[channel]} is already verified.")
    other = await _find_by_contact(channel, target)
    if other and other.id != user.id and is_verified(other, channel):
        raise HTTPException(
            status_code=409, detail=f"This {CONTACT_NAME[channel]} is already verified on another account."
        )
    return target


@router.post("/me/verify/send-otp")
@limiter.limit("5/minute")
async def send_verification_otp(
    request: Request, body: VerifyContactSendRequest, current_user: dict = Depends(get_current_user)
):
    """Send an OTP to the signed-in user's own (unverified) phone or email."""
    user = await _current(current_user)
    target = await _verification_target(user, body.channel)
    return _sent_payload(await send_otp(body.channel, target, "verify"))


@router.post("/me/verify", response_model=UserResponse)
@limiter.limit("10/minute")
async def verify_contact(
    request: Request, body: VerifyContactRequest, current_user: dict = Depends(get_current_user)
):
    """Mark the phone or email as verified, enabling login through that channel."""
    user = await _current(current_user)
    target = await _verification_target(user, body.channel)
    await verify_otp(body.channel, target, "verify", body.otp)
    if body.channel == SMS:
        user.phone_verified = True
    else:
        user.email_verified = True
    await user.save()
    return UserResponse(**_user_payload(user))


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout current user."""
    user = await User.get(current_user["id"])
    if user:
        user.refresh_token = None
        user.refresh_token_expiry = None
        await user.save()
    return {"success": True, "message": "Logged out successfully"}
