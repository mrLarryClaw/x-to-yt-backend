import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

@dataclass
class User:
    id: str
    google_sub: str
    email: str
    display_name: str
    avatar_url: Optional[str] = None
    is_allowed: bool = True
    email_verified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class OAuthToken:
    id: str
    user_id: str
    provider: str = "google"
    access_token: str = ""
    refresh_token: Optional[str] = None
    scope: str = ""
    expiry: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass
class Job:
    id: str
    user_id: str
    source_url: str
    canonical_url: str
    status: str = "queued"  # queued | downloading | uploading | completed | failed
    progress_stage: Optional[str] = None
    progress_pct: Optional[int] = None
    source_title: Optional[str] = None
    source_duration: Optional[int] = None
    download_path: Optional[str] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class InMemoryDB:
    def __init__(self):
        self._users: dict[str, User] = {}
        self._tokens: dict[str, OAuthToken] = {}  # keyed by user_id
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get_user_by_google_sub(self, google_sub: str) -> Optional[User]:
        for u in self._users.values():
            if u.google_sub == google_sub:
                return u
        return None

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)

    def upsert_user(self, user: User) -> User:
        self._users[user.id] = user
        return user

    def get_token_by_user(self, user_id: str) -> Optional[OAuthToken]:
        return self._tokens.get(user_id)

    def upsert_token(self, token: OAuthToken) -> None:
        self._tokens[token.user_id] = token

    def create_job(self, job: Job) -> Job:
        self._jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def get_jobs_by_user(self, user_id: str, limit: int = 20, offset: int = 0):
        user_jobs = [j for j in self._jobs.values() if j.user_id == user_id]
        user_jobs.sort(key=lambda j: j.created_at, reverse=True)
        return user_jobs[offset:offset + limit], len(user_jobs)

    def update_job(self, job: Job) -> Job:
        job.updated_at = datetime.now(timezone.utc)
        return job

    def delete_job(self, job_id: str) -> bool:
        return self._jobs.pop(job_id, None) is not None

db = InMemoryDB()