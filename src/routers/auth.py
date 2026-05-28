import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import RedirectResponse, JSONResponse

from src.database import db, User, OAuthToken
from src.services.google_auth import (
    create_authorization_url,
    exchange_code,
    fetch_user_info,
)
from src.services.crypto import crypto_service
from src.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google/start")
async def auth_start(request: Request):
    state = str(uuid.uuid4())
    response = RedirectResponse(url=create_authorization_url(state))
    response.set_cookie(
        key="auth_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/google/callback")
async def auth_callback(request: Request, code: str, state: str):
    cookie_state = request.cookies.get("auth_state")
    if not cookie_state or cookie_state != state:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth=rejected")

    try:
        tokens = await exchange_code(code, state)
    except Exception:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth=rejected")

    try:
        user_info = await fetch_user_info(tokens["access_token"])
    except Exception:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth=rejected")

    email = (user_info.get("email") or "").strip().lower()
    email_verified = bool(user_info.get("email_verified"))
    google_sub = user_info.get("sub", "")
    name = user_info.get("name", "")
    picture = user_info.get("picture", "")

    if not email_verified:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth=rejected")

    if email not in settings.allowed_emails_list:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth=rejected")

    # Find or create user
    user = db.get_user_by_google_sub(google_sub)
    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            google_sub=google_sub,
            email=email,
            display_name=name or email,
            avatar_url=picture,
            is_allowed=True,
            email_verified=True,
        )
        db.upsert_user(user)
    else:
        user.email = email
        user.display_name = name or email
        user.avatar_url = picture
        user.is_allowed = True
        user.email_verified = True
        user.updated_at = datetime.now(timezone.utc)

    # Store tokens (encrypted)
    encrypted_access = crypto_service.encrypt(tokens["access_token"])
    encrypted_refresh = crypto_service.encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None
    expiry = tokens.get("expiry")

    token = OAuthToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        provider="google",
        access_token=encrypted_access,
        refresh_token=encrypted_refresh,
        scope="openid email https://www.googleapis.com/auth/youtube.upload",
        expiry=expiry,
    )
    db.upsert_token(token)

    # Create session
    session_id = str(uuid.uuid4())
    if not hasattr(request.app.state, "sessions"):
        request.app.state.sessions = {}
    request.app.state.sessions[session_id] = user.id

    response = RedirectResponse(url=f"{settings.frontend_url}/?auth=success")
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400 * 7,
    )
    response.delete_cookie("auth_state")
    return response


@router.post("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id and hasattr(request.app.state, "sessions"):
        request.app.state.sessions.pop(session_id, None)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("session_id")
    return response


@router.get("/me")
async def me(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "is_allowed": user.is_allowed,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
    }