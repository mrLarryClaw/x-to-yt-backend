import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.database import get_db
from src.models import User, OAuthToken
from src.schemas import UserOut
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
async def auth_callback(request: Request, code: str, state: str, db: AsyncSession = Depends(get_db)):
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

    stmt = select(User).where(User.google_sub == google_sub)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if user is None:
        user = User(
            google_sub=google_sub,
            email=email,
            display_name=name or email,
            avatar_url=picture,
            is_allowed=True,
            email_verified=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        user.email = email
        user.display_name = name or email
        user.avatar_url = picture
        user.is_allowed = True
        user.email_verified = True
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)

    # Upsert tokens
    stmt_token = select(OAuthToken).where(OAuthToken.user_id == user.id)
    res_token = await db.execute(stmt_token)
    token_row = res_token.scalars().first()

    encrypted_access = crypto_service.encrypt(tokens["access_token"])
    encrypted_refresh = crypto_service.encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None
    expiry = tokens.get("expiry")

    if token_row is None:
        token_row = OAuthToken(
            user_id=user.id,
            provider="google",
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            scope=" ".join(["openid", "email", "https://www.googleapis.com/auth/youtube.upload"]),
            expiry=expiry,
        )
        db.add(token_row)
    else:
        token_row.access_token = encrypted_access
        if encrypted_refresh:
            token_row.refresh_token = encrypted_refresh
        token_row.expiry = expiry
        token_row.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Create session
    session_id = str(uuid.uuid4())
    if not hasattr(request.app.state, "sessions"):
        request.app.state.sessions = {}
    request.app.state.sessions[session_id] = str(user.id)

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


@router.get("/me", response_model=UserOut)
async def me(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return user
