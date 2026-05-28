import asyncio
from datetime import datetime, timezone, timedelta

import httpx
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from src.config import settings
from src.services.crypto import crypto_service

AUTH_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/youtube.upload",
]


def get_client_config():
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.redirect_uri],
        }
    }


def create_authorization_url(state: str) -> str:
    flow = Flow.from_client_config(
        get_client_config(),
        scopes=AUTH_SCOPES,
        redirect_uri=settings.redirect_uri,
    )
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return url


def _sync_exchange_code(code: str, state: str):
    flow = Flow.from_client_config(
        get_client_config(),
        scopes=AUTH_SCOPES,
        redirect_uri=settings.redirect_uri,
        state=state,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    expiry = creds.expiry
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expiry": expiry,
        "id_token": creds.id_token,
    }


async def exchange_code(code: str, state: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_exchange_code, code, state)


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