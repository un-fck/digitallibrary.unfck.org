"""API key management endpoints: signup, verify, info, rotate."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.deps import DBConn, RequiredKey
from api.models.api_key import (
    KeyInfo,
    KeyResponse,
    SignupRequest,
    UsageStats,
    VerifyRequest,
)
from api.services import key_service
from api.services.email_service import send_verification_email

router = APIRouter(tags=["api-keys"])


@router.post("/signup", response_model=dict)
async def signup(body: SignupRequest, conn: DBConn):
    """Request an API key. Sends a verification email."""
    token = await key_service.create_verify_token(
        conn,
        email=body.email,
        name=body.name,
        use_case=body.use_case,
    )
    try:
        send_verification_email(body.email, token)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to send verification email. Please try again later.",
        )
    return {"ok": True, "message": "Check your email for a verification link."}


@router.post("/verify", response_model=KeyResponse)
async def verify(body: VerifyRequest, conn: DBConn):
    """Verify email and receive your API key."""
    info = await key_service.verify_token(conn, body.token)
    if not info:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    api_user_id = await key_service.create_api_user(
        conn,
        email=info["email"],
        name=info["name"],
        use_case=info["use_case"],
    )
    raw_key = await key_service.create_key_for_user(conn, api_user_id)

    return KeyResponse(
        api_key=raw_key,
        key_prefix=key_service.key_prefix(raw_key),
        tier="free",
        rate_limit=60,
    )


@router.get("/me", response_model=KeyInfo)
async def key_info(key: RequiredKey, conn: DBConn):
    """Get info about your API key."""
    info = await key_service.get_key_info(conn, str(key["api_user_id"]))
    if not info:
        raise HTTPException(status_code=404, detail="No active key found")
    return KeyInfo(**info)


@router.get("/me/usage", response_model=UsageStats)
async def key_usage(key: RequiredKey, conn: DBConn):
    """Get usage statistics for your API key."""
    return UsageStats(**await key_service.get_usage(conn, str(key["api_user_id"])))


@router.post("/rotate", response_model=KeyResponse)
async def rotate(key: RequiredKey, conn: DBConn):
    """Revoke your current key and get a new one."""
    raw_key = await key_service.rotate_key(conn, str(key["api_user_id"]))
    return KeyResponse(
        api_key=raw_key,
        key_prefix=key_service.key_prefix(raw_key),
        tier=key["tier"],
        rate_limit=key["rate_limit"],
    )
