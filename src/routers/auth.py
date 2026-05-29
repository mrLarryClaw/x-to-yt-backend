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
    and redirects to frontend with session cookie."""
    
    frontend_url = settings.frontend_url or "https://x-to-yt-frontend-production.up.railway.app"
    
    if error == "access_denied":
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")
    
    if not code:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")
    
    # Verify state cookie (CSRF protection)
    cookie_state = request.cookies.get("auth_state")
    if not cookie_state:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")
    
    try:
        tokens = await exchange_code(code, state)
    except Exception:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")

    try:
        user_info = await fetch_user_info(tokens["access_token"])
    except Exception:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")

    email = (user_info.get("email") or "").strip().lower()
    email_verified = bool(user_info.get("email_verified"))
    google_sub = user_info.get("sub", "")
    name = user_info.get("name", "")
    picture = user_info.get("picture", "")

    if not email_verified:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")

    if email not in settings.allowed_emails_list:
        return RedirectResponse(url=f"{frontend_url}/?auth=rejected")

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

    # Redirect to frontend with session_id in URL (cross-domain cookies don't work)
    response = RedirectResponse(
        url=f"{frontend_url}/?auth=success&session_id={session_id}&name={user.display_name}&email={user.email}"
    )
    response.delete_cookie("auth_state")
    return response


@router.get("/session")
async def get_session(request: Request, session_id: str = None):
    """Verify a session_id and return user info."""
    if not session_id:
        # Also try header
        session_id = request.headers.get("X-Session-Id")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no_session")
    
    sessions = getattr(request.app.state, "sessions", {})
    user_id = sessions.get(session_id)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_not_found")
    
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "is_allowed": user.is_allowed,
        "email_verified": user.email_verified,
    }


@router.post("/google/callback")
async def auth_callback_post(request: Request, data: dict = None):
    """API endpoint for frontend to exchange code for session. 
    Alternative to GET callback for API-based auth flows."""
    
    if not data or not data.get("code"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_code")
    
    try:
        tokens = await exchange_code(data["code"], None)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    try:
        user_info = await fetch_user_info(tokens["access_token"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_info_failed")

    email = (user_info.get("email") or "").strip().lower()
    email_verified = bool(user_info.get("email_verified"))
    google_sub = user_info.get("sub", "")

    if not email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="email_not_verified")

    if email not in settings.allowed_emails_list:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_allowed")

    user = db.get_user_by_google_sub(google_sub)
    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            google_sub=google_sub,
            email=email,
            display_name=user_info.get("name", email),
            avatar_url=user_info.get("picture"),
            is_allowed=True,
            email_verified=True,
        )
        db.upsert_user(user)

    encrypted_access = crypto_service.encrypt(tokens["access_token"])
    encrypted_refresh = crypto_service.encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None

    token = OAuthToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        provider="google",
        access_token=encrypted_access,
        refresh_token=encrypted_refresh,
        scope="openid email https://www.googleapis.com/auth/youtube.upload",
        expiry=tokens.get("expiry"),
    )
    db.upsert_token(token)

    session_id = str(uuid.uuid4())
    if not hasattr(request.app.state, "sessions"):
        request.app.state.sessions = {}
    request.app.state.sessions[session_id] = user.id

    return {"session_id": session_id, "user": {"id": user.id, "email": user.email, "display_name": user.display_name}}


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