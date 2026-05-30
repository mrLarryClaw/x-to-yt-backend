import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from src.config import settings


# ── Data classes (kept for type compatibility) ──

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
    status: str = "queued"
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


# ── Schema DDL ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    google_sub TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    is_allowed BOOLEAN DEFAULT TRUE,
    email_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    provider TEXT DEFAULT 'google',
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    scope TEXT DEFAULT '',
    expiry TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
    progress_stage TEXT,
    progress_pct INTEGER,
    source_title TEXT,
    source_duration INTEGER,
    download_path TEXT,
    youtube_video_id TEXT,
    youtube_url TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user_id ON oauth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def _get_connection():
    """Get a psycopg2 connection from DATABASE_URL."""
    url = settings.psycopg_url
    return psycopg2.connect(url)


def _init_db():
    """Create tables if they don't exist."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _row_to_user(row: dict) -> User:
    return User(
        id=row["id"],
        google_sub=row["google_sub"],
        email=row["email"],
        display_name=row["display_name"],
        avatar_url=row.get("avatar_url"),
        is_allowed=row.get("is_allowed", True),
        email_verified=row.get("email_verified", False),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_token(row: dict) -> OAuthToken:
    return OAuthToken(
        id=row["id"],
        user_id=row["user_id"],
        provider=row.get("provider", "google"),
        access_token=row["access_token"],
        refresh_token=row.get("refresh_token"),
        scope=row.get("scope", ""),
        expiry=row.get("expiry"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_job(row: dict) -> Job:
    return Job(
        id=row["id"],
        user_id=row["user_id"],
        source_url=row["source_url"],
        canonical_url=row["canonical_url"],
        status=row.get("status", "queued"),
        progress_stage=row.get("progress_stage"),
        progress_pct=row.get("progress_pct"),
        source_title=row.get("source_title"),
        source_duration=row.get("source_duration"),
        download_path=row.get("download_path"),
        youtube_video_id=row.get("youtube_video_id"),
        youtube_url=row.get("youtube_url"),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        updated_at=row["updated_at"],
    )


class PostgresDB:
    """PostgreSQL-backed database with the same interface as InMemoryDB."""

    def __init__(self):
        self._lock = threading.Lock()

    def _query(self, sql: str, params=None, fetch=True) -> list[dict]:
        conn = _get_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall() if fetch else []
            conn.commit()
            cur.close()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _execute(self, sql: str, params=None):
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def get_user_by_google_sub(self, google_sub: str) -> Optional[User]:
        rows = self._query("SELECT * FROM users WHERE google_sub = %s", (google_sub,))
        return _row_to_user(rows[0]) if rows else None

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        rows = self._query("SELECT * FROM users WHERE id = %s", (user_id,))
        return _row_to_user(rows[0]) if rows else None

    def get_user(self, user_id: str) -> Optional[User]:
        return self.get_user_by_id(user_id)

    def upsert_user(self, user: User) -> User:
        self._execute(
            """INSERT INTO users (id, google_sub, email, display_name, avatar_url, is_allowed, email_verified, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (google_sub) DO UPDATE SET
                 email = EXCLUDED.email,
                 display_name = EXCLUDED.display_name,
                 avatar_url = EXCLUDED.avatar_url,
                 is_allowed = EXCLUDED.is_allowed,
                 email_verified = EXCLUDED.email_verified,
                 updated_at = EXCLUDED.updated_at""",
            (user.id, user.google_sub, user.email, user.display_name, user.avatar_url,
             user.is_allowed, user.email_verified, user.created_at, user.updated_at),
        )
        return user

    def get_token_by_user(self, user_id: str) -> Optional[OAuthToken]:
        rows = self._query("SELECT * FROM oauth_tokens WHERE user_id = %s", (user_id,))
        return _row_to_token(rows[0]) if rows else None

    def upsert_token(self, token: OAuthToken) -> None:
        self._execute(
            """INSERT INTO oauth_tokens (id, user_id, provider, access_token, refresh_token, scope, expiry, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET
                 access_token = EXCLUDED.access_token,
                 refresh_token = EXCLUDED.refresh_token,
                 scope = EXCLUDED.scope,
                 expiry = EXCLUDED.expiry,
                 updated_at = EXCLUDED.updated_at""",
            (token.id, token.user_id, token.provider, token.access_token,
             token.refresh_token, token.scope, token.expiry, token.created_at, token.updated_at),
        )

    def create_job(self, job: Job) -> Job:
        self._execute(
            """INSERT INTO jobs (id, user_id, source_url, canonical_url, status, progress_stage, progress_pct,
                  source_title, source_duration, download_path, youtube_video_id, youtube_url,
                  error_code, error_message, created_at, started_at, completed_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (job.id, job.user_id, job.source_url, job.canonical_url, job.status,
             job.progress_stage, job.progress_pct, job.source_title, job.source_duration,
             job.download_path, job.youtube_video_id, job.youtube_url,
             job.error_code, job.error_message, job.created_at, job.started_at,
             job.completed_at, job.updated_at),
        )
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        rows = self._query("SELECT * FROM jobs WHERE id = %s", (job_id,))
        return _row_to_job(rows[0]) if rows else None

    def get_jobs_by_user(self, user_id: str, limit: int = 20, offset: int = 0):
        rows = self._query(
            "SELECT * FROM jobs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        )
        count_rows = self._query("SELECT COUNT(*) as cnt FROM jobs WHERE user_id = %s", (user_id,))
        total = count_rows[0]["cnt"] if count_rows else 0
        return [_row_to_job(r) for r in rows], total

    def update_job(self, job: Job) -> Job:
        job.updated_at = datetime.now(timezone.utc)
        self._execute(
            """UPDATE jobs SET
                 status = %s, progress_stage = %s, progress_pct = %s,
                 source_title = %s, source_duration = %s, download_path = %s,
                 youtube_video_id = %s, youtube_url = %s,
                 error_code = %s, error_message = %s,
                 started_at = %s, completed_at = %s, updated_at = %s
               WHERE id = %s""",
            (job.status, job.progress_stage, job.progress_pct,
             job.source_title, job.source_duration, job.download_path,
             job.youtube_video_id, job.youtube_url,
             job.error_code, job.error_message,
             job.started_at, job.completed_at, job.updated_at, job.id),
        )
        return job

    def delete_job(self, job_id: str) -> bool:
        result = self._query("DELETE FROM jobs WHERE id = %s RETURNING id", (job_id,))
        return len(result) > 0

    def get_next_queued_job(self) -> Optional[Job]:
        """Get the oldest queued job (for worker tick)."""
        rows = self._query("SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1")
        return _row_to_job(rows[0]) if rows else None

    # ── Session helpers (replaces in-memory app.state.sessions) ──

    def create_session(self, session_id: str, user_id: str) -> None:
        self._execute(
            "INSERT INTO sessions (session_id, user_id) VALUES (%s, %s) ON CONFLICT (session_id) DO UPDATE SET user_id = EXCLUDED.user_id",
            (session_id, user_id),
        )

    def get_session_user_id(self, session_id: str) -> Optional[str]:
        rows = self._query("SELECT user_id FROM sessions WHERE session_id = %s", (session_id,))
        return rows[0]["user_id"] if rows else None

    def delete_session(self, session_id: str) -> None:
        self._execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))


# Initialize on module load
db = PostgresDB()
_init_db()