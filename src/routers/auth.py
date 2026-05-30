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
    """Initiate Google OAuth flow. Redirects user to Google consent screen."""
    state = str(uuid.uuid4())
    auth_url = create_authorization_url(state)
    response = RedirectResponse(url=auth_url)
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
async def auth_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Handle Google OAuth callback. Exchanges code for tokens, creates session,
    and redirects to frontend with session_id in URL params."""
    
    frontend_url = settings.frontend_url or "https://x-to-yt-frontend-production.up.railway.app"
    
    if error:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected&reason=google_{error}")
    
    if not code:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected&reason=no_code")
    
    try:
        tokens = await exchange_code(code)
    except Exception as e:
        import logging
        logging.error(f"Token exchange failed: {type(e).__name__}: {e}")
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected&reason=exchange_failed&detail={str(e)[:200]}")

    try:
        user_info = await fetch_user_info(tokens["access_token"])
    except Exception as e:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected&reason=user_info_failed")

    email = (user_info.get("email") or "").strip().lower()
    email_verified = bool(user_info.get("email_verified"))
    google_sub = user_info.get("sub", "")
    name = user_info.get("name", "")
    picture = user_info.get("picture", "")

    if not email_verified:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected&reason=email_not_verified")

    if email not in settings.allowed_emails_list:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected&reason=email_not_allowed&email={email}")

    # Find or create user (persisted in PostgreSQL)
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
    else:
        user.email = email
        user.display_name = name or email
        user.avatar_url = picture
        user.is_allowed = True
        user.email_verified = True
        user.updated_at = datetime.now(timezone.utc)

    db.upsert_user(user)

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

    # Create session (persisted in PostgreSQL)
    session_id = str(uuid.uuid4())
    db.create_session(session_id, user.id)

    # Redirect to frontend with session_id in URL
    response = RedirectResponse(
        url=f"{frontend_url}/?auth=success&session_id={session_id}&name={user.display_name}&email={user.email}"
    )
    response.delete_cookie("auth_state")
    return response


@router.post("/logout")
async def logout(request: Request):
    # Try to get session from header or cookie
    auth_header = request.headers.get("Authorization", "")
    session_id = None
    if auth_header.startswith("Bearer "):
        session_id = auth_header[7:].strip()
    if not session_id:
        session_id = request.cookies.get("session_id")
    if session_id:
        db.delete_session(session_id)
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