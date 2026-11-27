"""Google Sign-In, OTP authentication, sessions and verified profile changes."""

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.security import create_access_token, generate_refresh_token, get_refresh_token_expiry, hash_refresh_token
from app.models.user import User
from app.schemas.auth import (
    GoogleSignInRequest, RegisterRequest, LoginRequest, RefreshRequest, SendOtpRequest, TokenResponse, UserResponse,
    VerifyContactRequest, VerifyContactSendRequest,
)
from app.middleware.auth import get_current_user
from app.middleware.rate_limiter import limiter
from app.services.google_sign_in import GoogleIdentity, verify_google_token
from app.services.otp_service import EMAIL, OtpSent, normalize_email, send_otp, verify_otp

router = APIRouter(prefix="/auth", tags=["Auth"])

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _find_by_email(email: str) -> Optional[User]:
    """Case-insensitive: older accounts may have stored the email with capitals."""
    matches = await User.find(
        {"email": {"$regex": f"^{re.escape(normalize_email(email))}$", "$options": "i"}}
    ).limit(2).to_list()
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="Contact support to verify your account.")
    return matches[0] if matches else None


def _user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "emailVerified": bool(user.email_verified),
        "googleLinked": bool(user.google_subject),
    }


def _sent_payload(sent: OtpSent) -> dict:
    return {
        "success": True,
        "message": f"OTP sent to {sent.destination}",
        "delivered": True,
        "testOtp": None,
        "channel": EMAIL,
        "destination": sent.destination,
        "otpLength": sent.otp_length,
        "expiresInSeconds": sent.expires_in_seconds,
        "resendAfterSeconds": sent.resend_after_seconds,
    }


async def _patch_user(user: User, changes: dict, *, expected: dict | None = None) -> User:
    saved = await User.get_motor_collection().find_one_and_update(
        {"_id": user.id, "isDeleted": {"$ne": True}, **(expected or {})},
        {"$set": changes},
        return_document=ReturnDocument.AFTER,
    )
    if saved is None:
        raise HTTPException(status_code=409, detail="Your account changed. Please try again.")
    return User.model_validate(saved)


async def _issue_tokens(
    user: User, *, changes: dict | None = None, expected: dict | None = None
) -> TokenResponse:
    refresh_token = generate_refresh_token()
    user = await _patch_user(
        user,
        {
            **(changes or {}),
            "refreshToken": hash_refresh_token(refresh_token),
            "refreshTokenExpiry": get_refresh_token_expiry(),
        },
        expected=expected,
    )
    return TokenResponse(
        token=create_access_token(str(user.id)),
        refreshToken=refresh_token,
        user=_user_payload(user),
    )


async def _check_not_registered(phone: Optional[str], email: str) -> None:
    if await _find_by_email(email):
        raise HTTPException(status_code=400, detail="This email address is already registered. Please login.")
    if phone and await User.find_one(User.phone == phone):
        raise HTTPException(status_code=400, detail="This mobile number is already registered. Please login.")


# ─── Login / registration ─────────────────────────────────────────────────────


@router.post("/send-otp")
@limiter.limit(lambda: settings.OTP_SEND_RATE_LIMIT)
async def send_otp_route(request: Request, body: SendOtpRequest):
    """Send a login or registration OTP by email."""
    return _sent_payload(await send_otp(normalize_email(body.email), body.purpose))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: settings.OTP_VERIFY_RATE_LIMIT)
async def register(request: Request, body: RegisterRequest):
    """Create an account once the emailed OTP is verified."""
    email = normalize_email(body.email)
    await verify_otp(email, "register", body.otp)
    await _check_not_registered(body.phone, email)

    user = User(
        name=body.name.strip(),
        email=email,
        phone=body.phone,
        email_verified=True,
        created_by="system",
    )
    try:
        await user.insert()
    except DuplicateKeyError:  # registered concurrently
        raise HTTPException(status_code=400, detail="This email address is already registered. Please login.")
    return await _issue_tokens(user)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(lambda: settings.OTP_VERIFY_RATE_LIMIT)
async def login(request: Request, body: LoginRequest):
    """Log in with an emailed OTP."""
    target = normalize_email(body.email)
    # Complete the same challenge for all valid email addresses before checking account presence.
    await verify_otp(target, "login", body.otp)
    user = await _find_by_email(target)
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this email address. Please register first.")
    return await _issue_tokens(user, changes={"emailVerified": True}, expected={"email": user.email})


def _google_link_required() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail="Verify this email with Email OTP, then link Google from your profile.",
    )


async def _link_google(user: User, identity: GoogleIdentity) -> User:
    if not user.email_verified or normalize_email(user.email) != identity.email:
        raise _google_link_required()
    try:
        return await _patch_user(
            user,
            {"googleSubject": identity.subject},
            expected={
                "email": user.email,
                "emailVerified": True,
                "$or": [{"googleSubject": None}, {"googleSubject": identity.subject}],
            },
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This Google account is already linked.") from None


@router.post("/google", response_model=TokenResponse)
@limiter.limit(lambda: settings.OTP_VERIFY_RATE_LIMIT)
async def google_login(request: Request, body: GoogleSignInRequest):
    """Sign in by verified Google subject, with safe account matching for first use."""
    identity = await verify_google_token(body.idToken)
    user = await User.find_one(User.google_subject == identity.subject)
    if user:
        return await _issue_tokens(user)
    if not identity.authoritative_email:
        raise _google_link_required()

    user = await _find_by_email(identity.email)
    if user:
        return await _issue_tokens(await _link_google(user, identity))
    try:
        user = await User(
            name=identity.name,
            email=identity.email,
            email_verified=True,
            google_subject=identity.subject,
            created_by="system",
        ).insert()
    except DuplicateKeyError:
        user = await User.find_one(User.google_subject == identity.subject)
        if not user:
            matching_email = await _find_by_email(identity.email)
            if not matching_email:
                raise _google_link_required() from None
            user = await _link_google(matching_email, identity)
    return await _issue_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """Refresh the access token using a valid refresh token."""
    token = request.refreshToken
    if not token or len(token) > 256 or token.startswith("sha256:"):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Legacy sessions migrate to hashed storage on their next refresh.
    user = await User.find_one({
        "refreshToken": {"$in": [hash_refresh_token(token), token]},
        "isDeleted": {"$ne": True},
    })
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

    try:
        return await _issue_tokens(user, expected={"refreshToken": user.refresh_token})
    except HTTPException as exc:
        if exc.status_code == 409:
            raise HTTPException(status_code=401, detail="Invalid refresh token") from None
        raise


# ─── Current user ─────────────────────────────────────────────────────────────


async def _current(current_user: dict) -> User:
    user = await User.get(current_user["id"])
    if not user or user.is_deleted:
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
    otp: str = Field(min_length=1, max_length=32)
    name: Optional[str] = None


@router.put("/me", response_model=UserResponse)
async def update_me(request: UpdateProfileRequest, current_user: dict = Depends(get_current_user)):
    """Update the profile name. Email changes use the verified change-email flow."""
    user = await _current(current_user)
    if normalize_email(request.email) != normalize_email(user.email):
        raise HTTPException(status_code=400, detail="Verify your new email address before changing it.")
    user = await _patch_user(user, {"name": request.name}, expected={"email": user.email})
    return UserResponse(**_user_payload(user))


@router.post("/me/google", response_model=UserResponse)
@limiter.limit(lambda: settings.OTP_VERIFY_RATE_LIMIT)
async def link_google(
    request: Request, body: GoogleSignInRequest, current_user: dict = Depends(get_current_user)
):
    user = await _current(current_user)
    identity = await verify_google_token(body.idToken)
    return UserResponse(**_user_payload(await _link_google(user, identity)))


@router.post("/me/change-email/send-otp")
@limiter.limit(lambda: settings.OTP_SEND_RATE_LIMIT)
async def send_change_email_otp(
    request: Request, body: ChangeEmailSendOtpRequest, current_user: dict = Depends(get_current_user)
):
    """Send an OTP to verify a new email address before updating profile."""
    user = await _current(current_user)
    new_email = normalize_email(body.newEmail)
    return _sent_payload(await send_otp(new_email, f"change_email:{user.id}"))


@router.post("/me/change-email/verify", response_model=UserResponse)
@limiter.limit(lambda: settings.OTP_VERIFY_RATE_LIMIT)
async def verify_change_email(
    request: Request, body: ChangeEmailVerifyRequest, current_user: dict = Depends(get_current_user)
):
    """Verify OTP and update user's email address and name."""
    user = await _current(current_user)
    new_email = normalize_email(body.newEmail)
    await verify_otp(new_email, f"change_email:{user.id}", body.otp)
    existing = await _find_by_email(new_email)
    if existing and existing.id != user.id:
        raise HTTPException(status_code=400, detail="This email address is already in use by another account.")
    changes: dict = {"email": new_email, "emailVerified": True}
    if body.name and body.name.strip():
        changes["name"] = body.name.strip()
    try:
        user = await _patch_user(user, changes, expected={"email": user.email})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="This email address is already in use.") from None
    return UserResponse(**_user_payload(user))


def _verification_target(user: User) -> str:
    target = normalize_email(user.email)
    if not target:
        raise HTTPException(status_code=400, detail="Please add an email address to your profile first.")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Your email address is already verified.")
    return target


@router.post("/me/verify/send-otp")
@limiter.limit(lambda: settings.OTP_SEND_RATE_LIMIT)
async def send_verification_otp(
    request: Request, body: VerifyContactSendRequest, current_user: dict = Depends(get_current_user)
):
    """Send an OTP to the signed-in user's unverified email address."""
    user = await _current(current_user)
    return _sent_payload(await send_otp(_verification_target(user), f"verify:{user.id}"))


@router.post("/me/verify", response_model=UserResponse)
@limiter.limit(lambda: settings.OTP_VERIFY_RATE_LIMIT)
async def verify_contact(
    request: Request, body: VerifyContactRequest, current_user: dict = Depends(get_current_user)
):
    """Mark the signed-in user's email address as verified."""
    user = await _current(current_user)
    target = _verification_target(user)
    await verify_otp(target, f"verify:{user.id}", body.otp)
    other = await _find_by_email(target)
    if other and other.id != user.id and other.email_verified:
        raise HTTPException(status_code=409, detail="This email address is already verified on another account.")
    user = await _patch_user(user, {"emailVerified": True}, expected={"email": user.email})
    return UserResponse(**_user_payload(user))


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout current user."""
    user = await User.get(current_user["id"])
    if user and not user.is_deleted:
        await _patch_user(user, {"refreshToken": None, "refreshTokenExpiry": None})
    return {"success": True, "message": "Logged out successfully"}
