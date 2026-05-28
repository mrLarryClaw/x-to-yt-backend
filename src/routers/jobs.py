import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Request

from src.database import db, Job
from src.utils.url_validator import normalize_url, is_valid_status_url

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def require_auth(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return user


@router.post("")
async def create_job(data: dict, request: Request):
    user = require_auth(request)
    url = (data.get("sourceUrl") or "").strip()
    if not is_valid_status_url(url):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_url")

    canonical = normalize_url(url)

    job = Job(
        id=str(uuid.uuid4()),
        user_id=user.id,
        source_url=url,
        canonical_url=canonical,
        status="queued",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.create_job(job)
    return _job_out(job)


@router.get("")
async def list_jobs(request: Request, limit: int = 20, offset: int = 0):
    user = require_auth(request)
    jobs, total = db.get_jobs_by_user(user.id, limit=limit, offset=offset)
    return {
        "jobs": [_job_out(j) for j in jobs],
        "total": total,
    }


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request):
    user = require_auth(request)
    job = db.get_job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return _job_out(job)


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    user = require_auth(request)
    job = db.get_job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if job.status != "failed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only_failed_jobs_can_be_retried")

    job.status = "queued"
    job.error_code = None
    job.error_message = None
    job.progress_stage = None
    job.progress_pct = None
    job.started_at = None
    job.completed_at = None
    job.updated_at = datetime.now(timezone.utc)
    db.update_job(job)
    return _job_out(job)


@router.delete("/{job_id}")
async def delete_job(job_id: str, request: Request):
    user = require_auth(request)
    job = db.get_job(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if job.status not in ("queued", "failed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_delete_active_job")
    db.delete_job(job_id)
    return None


def _job_out(job: Job) -> dict:
    return {
        "id": job.id,
        "user_id": job.user_id,
        "source_url": job.source_url,
        "canonical_url": job.canonical_url,
        "status": job.status,
        "progress_stage": job.progress_stage,
        "progress_pct": job.progress_pct,
        "source_title": job.source_title,
        "source_duration": job.source_duration,
        "download_path": job.download_path,
        "youtube_video_id": job.youtube_video_id,
        "youtube_url": job.youtube_url,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }