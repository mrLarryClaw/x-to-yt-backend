# X-to-YouTube Backend — Specification

## Overview

Personal backend service that downloads X.com videos and uploads them to the user's private YouTube account via OAuth. Single-user, allowlist-protected, deployed on Railway.

---

## Architecture

- **API:** FastAPI (Python 3.11+)
- **Worker:** Background thread using APScheduler (same process)
- **Database:** PostgreSQL via Railway
- **Auth:** Google OAuth 2.0 (web-server flow)
- **Download:** yt-dlp
- **Upload:** google-api-python-client (YouTube Data API v3, resumable upload)
- **Token storage:** Encrypted at rest with Fernet (symmetric)

---

## Environment Variables

| Variable | Description | Example |
|---|---|---|
| `ALLOWED_EMAILS` | Comma-separated allowed Google emails | `you@example.com,spouse@example.com` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | `123.apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | `secret` |
| `REDIRECT_URI` | OAuth callback (frontend's callback route) | `https://x-to-yt-frontend.vercel.app/api/auth/callback` |
| `FRONTEND_URL` | Frontend base URL | `https://x-to-yt-frontend.vercel.app` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@host:5432/db` |
| `SECRET_KEY` | 32-byte base64 Fernet key for token encryption | `base64...` |
| `SESSION_SECRET` | Secret for signing session cookies | `random-string` |

---

## Data Models

### User
```
id              UUID (PK)
google_sub      String (unique, from Google)
email           String (unique)
display_name    String
avatar_url      String (nullable)
is_allowed      Boolean (default False)
email_verified  Boolean (default False)
created_at      DateTime
updated_at      DateTime
```

### OAuthToken
```
id              UUID (PK)
user_id         UUID (FK → User)
provider        String ("google")
access_token    String (encrypted)
refresh_token   String (encrypted, nullable)
scope           String
expiry          DateTime (nullable)
created_at      DateTime
updated_at      DateTime
```

### Job
```
id              UUID (PK)
user_id         UUID (FK → User)
source_url      String
canonical_url   String (normalized)
status          Enum: queued | downloading | uploading | completed | failed
progress_stage  String (nullable)
progress_pct    Integer (0-100, nullable)
source_title    String (nullable)
source_duration Integer (nullable, seconds)
download_path   String (nullable, local file path)
youtube_video_id String (nullable)
youtube_url     String (nullable)
error_code      String (nullable)
error_message   String (nullable)
created_at      DateTime
started_at      DateTime (nullable)
completed_at    DateTime (nullable)
updated_at      DateTime
```

### JobEvent
```
id              UUID (PK)
job_id          UUID (FK → Job)
event_type      String
message         String
metadata_json   JSON (nullable)
created_at      DateTime
```

---

## API Endpoints

### Auth

#### `GET /api/auth/google/start`
Redirects user to Google OAuth consent screen.
- Query param: `state` (CSRF token, echoed back)
- Sets session cookie before redirect
- Scopes: `openid email https://www.googleapis.com/auth/youtube.upload`

#### `GET /api/auth/google/callback`
Handles OAuth callback from Google.
1. Exchange authorization code for tokens
2. Fetch Google user info (`email`, `email_verified`, `sub`, `name`, `picture`)
3. Verify `email_verified == true`
4. Verify email in `ALLOWED_EMAILS` (case-insensitive)
5. If allowed: create/update User record, store encrypted OAuth tokens, create session, redirect to `FRONTEND_URL/?auth=success`
6. If not allowed: redirect to `FRONTEND_URL/?auth=rejected`

#### `POST /api/auth/logout`
- Clear session cookie
- Return 200

#### `GET /api/me`
- Return current authenticated user JSON or 401

### Jobs

#### `POST /api/jobs`
Create a new job. Requires auth.
- Body: `{ "sourceUrl": "https://x.com/user/status/123" }`
- Validate URL is X.com/twitter.com status URL (not profile, space, list)
- Create job with `status=queued`
- Return job JSON

#### `GET /api/jobs`
List current user's jobs. Requires auth.
- Query params: `limit` (default 20), `offset` (default 0)
- Return `{ "jobs": [...], "total": N }`

#### `GET /api/jobs/:id`
Get job detail. Requires auth (owner only).
- Return job JSON with events

#### `POST /api/jobs/:id/retry`
Retry a failed job. Requires auth (owner only).
- Reset status to `queued`, clear error fields
- Return updated job

#### `DELETE /api/jobs/:id`
Delete a job. Requires auth (owner only).
- Only allowed if job is `queued` or `failed`
- Return 204

---

## Worker (Background Job)

### Scheduler
- APScheduler runs every 30 seconds
- Claims one `queued` job (oldest first, by created_at)
- Updates job status → `downloading`

### Download Step
1. Run `yt-dlp [source_url] -o /tmp/{job_id}.mp4 --no-playlist`
2. Capture stdout/stderr for logging
3. On failure: update `error_code=download_failed`, `error_message=<reason>`, status → `failed`
4. On success: store `download_path`, update status → `uploading`

### Upload Step
1. Get user's OAuth tokens (decrypted)
2. If `access_token` expired and `refresh_token` exists, refresh
3. Use `googleapiclient` to initiate resumable upload to YouTube
4. Set `status.privacyStatus=private`
5. Poll for completion
6. On success: store `youtube_video_id`, `youtube_url`, status → `completed`
7. Delete temp download file
8. On failure: update `error_code=upload_failed`, `error_message=<reason>`, status → `failed`

### Cleanup
- Temp files deleted after successful upload
- Failed jobs keep temp files for retry (can be cleaned after 7 days via cron)

---

## Security

1. **Allowlist enforcement:** Any Google account not in `ALLOWED_EMAILS` is rejected at OAuth callback
2. **Email verification:** Must have `email_verified=true` from Google
3. **Token encryption:** OAuth tokens stored encrypted with Fernet (key from `SECRET_KEY` env)
4. **URL validation:** Only X.com/twitter.com status URLs accepted; reject profiles/spaces/lists
5. **HTTPS:** All deployed traffic over HTTPS
6. **No secrets in logs:** Redact tokens, Authorization headers from log output
7. **Rate limiting:** Job creation limited to 10/min per user (simple in-memory or Redis)

---

## Deployment

### Railway Setup
1. Create new Railway project
2. Provision PostgreSQL database
3. Add environment variables from table above
4. Connect GitHub repo (mrLarryClaw/x-to-yt-backend)
5. Deploy: build `pip install -r requirements.txt`, start `uvicorn src.main:app --host 0.0.0.0 --port $PORT`

### File Structure
```
x-to-yt-backend/
├── SPEC.md
├── requirements.txt
├── .env.example
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── main.py          # FastAPI app, routes, startup
│   ├── config.py        # Env var loading
│   ├── database.py      # SQLAlchemy engine, session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── token.py
│   │   ├── job.py
│   │   └── job_event.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   └── job.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   └── jobs.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── google_auth.py
│   │   ├── youtube.py
│   │   └── crypto.py
│   ├── worker/
│   │   ├── __init__.py
│   │   └── scheduler.py
│   └── utils/
│       ├── __init__.py
│       └── url_validator.py
└── tests/
    ├── __init__.py
    ├── test_auth.py
    ├── test_jobs.py
    └── test_url_validator.py
```

---

## Job Status States

| Status | Meaning |
|---|---|
| `queued` | Job created, waiting for worker |
| `downloading` | Worker is downloading via yt-dlp |
| `uploading` | Worker is uploading to YouTube |
| `completed` | Video uploaded successfully |
| `failed` | Download or upload failed (retry available) |

---

## Error Codes

| Code | Meaning |
|---|---|
| `invalid_url` | URL doesn't match X.com status pattern |
| `download_failed` | yt-dlp failed (network error, video unavailable, etc.) |
| `upload_failed` | YouTube API error (auth expired, quota exceeded, etc.) |
| `not_allowed` | User's Google email not in allowlist |
| `unauthorized` | No valid session |

---

## Out of Scope (v1)

- Multi-user / public access
- iOS-specific UX
- Bulk import / playlist support
- Browser extension
- Public sharing features
- Push notifications
- SSE / WebSocket (polling only)