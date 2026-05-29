import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx
import requests as req_lib
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from src.config import settings
from src.services.crypto import crypto_service

AUTH_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/youtube.upload",
]


def create_authorization_url(state: str) -> str:
    """Build Google OAuth URL manually — no PKCE code_challenge
    (we use client_secret for security, and PKCE breaks across redirects)."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": " ".join(AUTH_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"


def _sync_exchange_code(code: str):
    """Exchange auth code for tokens via direct POST to Google's token endpoint.
    No PKCE — just client_id + client_secret + redirect_uri."""
    resp = req_lib.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    expiry = None
    if data.get("expires_in"):
        expiry = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expiry": expiry,
        "id_token": data.get("id_token"),
    }


async def exchange_code(code: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_exchange_code, code)


async def fetch_user_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )
    creds.refresh(GoogleRequest())
    expiry = creds.expiry
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return {
        "access_token": creds.token,
        "expiry": expiry,
    }


async def refresh_if_needed(token) -> str:
    """token is an OAuthToken dataclass from database.py"""
    expiry = token.expiry
    needs_refresh = (
        expiry is None or
        expiry < datetime.now(timezone.utc) + timedelta(seconds=60)
    )
    if needs_refresh and token.refresh_token:
        decrypted_refresh = crypto_service.decrypt(token.refresh_token)
        loop = asyncio.get_running_loop()
        refreshed = await loop.run_in_executor(None, refresh_access_token, decrypted_refresh)
        token.access_token = crypto_service.encrypt(refreshed["access_token"])
        token.expiry = refreshed["expiry"]
        if refreshed.get("refresh_token"):
            token.refresh_token = crypto_service.encrypt(refreshed["refresh_token"])
        return refreshed["access_token"]
    return crypto_service.decrypt(token.access_token)