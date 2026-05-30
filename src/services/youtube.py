import os
import json
import requests
import logging
from typing import Optional, Callable

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

logger = logging.getLogger("youtube")


def upload_video_resumable(
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    file_path: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    privacy_status: str = "private",
) -> str:
    """Upload video to YouTube using direct resumable upload via requests.
    Avoids httplib2 entirely to prevent the 'Redirected but missing Location' bug."""
    
    # Build credentials and refresh if needed
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    if creds.expired and refresh_token:
        creds.refresh(GoogleAuthRequest())
    
    token = creds.token
    
    # Step 1: Initiate resumable upload
    metadata = {
        "snippet": {
            "title": title or "Uploaded from X",
            "description": description or "",
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    
    initiate_url = (
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable"
        "&part=snippet,status"
    )
    
    file_size = os.path.getsize(file_path)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(file_size),
    }
    
    resp = requests.post(initiate_url, headers=headers, json=metadata, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload initiation failed: {resp.status_code} {resp.text[:500]}")
    
    upload_url = resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError(f"YouTube upload initiation succeeded but no Location header: {resp.headers}")
    
    # Step 2: Upload the file in chunks
    chunk_size = 5 * 1024 * 1024  # 5 MB
    import logging
    logger = logging.getLogger("youtube")
    
    with open(file_path, "rb") as f:
        offset = 0
        while offset < file_size:
            f.seek(offset)
            chunk = f.read(chunk_size)
            chunk_len = len(chunk)
            end = min(offset + chunk_len, file_size) - 1
            
            put_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {offset}-{end}/{file_size}",
            }
            
            put_resp = requests.put(upload_url, headers=put_headers, data=chunk, timeout=300)
            
            if put_resp.status_code == 200 or put_resp.status_code == 201:
                # Upload complete
                result = put_resp.json()
                video_id = result.get("id")
                if not video_id:
                    raise RuntimeError(f"YouTube upload response missing video id: {put_resp.text[:200]}")
                logger.info(f"YouTube upload complete: video_id={video_id}")
                return video_id
            elif put_resp.status_code == 308:
                # Resume incomplete — get the last byte received
                range_header = put_resp.headers.get("Range", "")
                if range_header:
                    # Range: bytes=0-LAST_BYTE
                    last_byte = int(range_header.split("-")[1])
                    offset = last_byte + 1
                    logger.info(f"Upload progress: {offset}/{file_size} bytes ({offset*100//file_size}%)")
                else:
                    # No range header, retry from current offset
                    offset += chunk_len
            elif put_resp.status_code >= 500:
                # Server error, retry from current position
                logger.warning(f"YouTube upload server error {put_resp.status_code}, retrying from offset {offset}")
                continue
            else:
                raise RuntimeError(f"YouTube upload chunk failed: {put_resp.status_code} {put_resp.text[:500]}")
    
    raise RuntimeError("YouTube upload: unexpected end of upload loop")


def upload_video(
    access_token_decrypted: str,
    file_path: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    privacy_status: str = "private",
    progress_callback: Optional[Callable[[int], None]] = None,
    refresh_token: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> str:
    """Upload video to YouTube. Uses direct resumable upload to avoid httplib2 bugs."""
    if not refresh_token or not client_id or not client_secret:
        raise RuntimeError("YouTube upload requires refresh_token, client_id, and client_secret")
    
    return upload_video_resumable(
        access_token=access_token_decrypted,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        file_path=file_path,
        title=title,
        description=description,
        privacy_status=privacy_status,
    )


def delete_youtube_video(
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    video_id: str,
) -> bool:
    """Delete a video from YouTube using the Data API v3."""
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    # Always refresh — access tokens expire fast, and Credentials.expired is unreliable
    creds.refresh(GoogleAuthRequest())

    url = f"https://www.googleapis.com/youtube/v3/videos?id={video_id}"
    headers = {"Authorization": f"Bearer {creds.token}"}
    resp = requests.delete(url, headers=headers, timeout=30)

    if resp.status_code == 204 or resp.status_code == 200:
        logger.info(f"YouTube video {video_id} deleted successfully")
        return True
    else:
        logger.error(f"YouTube delete failed: {resp.status_code} {resp.text[:500]}")
        raise RuntimeError(f"YouTube delete failed: {resp.status_code} {resp.text[:200]}")