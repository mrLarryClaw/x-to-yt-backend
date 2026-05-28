import os
from pathlib import Path
from typing import Optional, Callable

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_httplib2 import AuthorizedHttp
import httplib2
from google.oauth2.credentials import Credentials

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


def get_youtube_service(access_token: str):
    creds = Credentials(access_token)
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=300))
    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, http=http, static_discovery=False)


def upload_video(
    access_token_decrypted: str,
    file_path: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    privacy_status: str = "private",
    progress_callback: Optional[Callable[[int], None]] = None,
) -> str:
    youtube = get_youtube_service(access_token_decrypted)

    body = {
        "snippet": {
            "title": title or "Uploaded from X",
            "description": description or "",
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        file_path,
        chunksize=1024 * 1024 * 5,  # 5 MB chunks
        mimetype="video/mp4",
        resumable=True,
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk(num_retries=3)
        if status and progress_callback:
            progress_callback(int(status.progress() * 100))

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("YouTube upload response missing video id")
    return video_id
