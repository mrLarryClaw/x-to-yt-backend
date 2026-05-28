import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.database import get_db
from src.models import User, Job, JobEvent, JobStatus
from src.schemas import JobOut, JobListOut, JobCreateIn
from src.utils.url_validator import normalize_url, is_valid_status_url

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

def require_auth(request: Request) -> User:
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return user

@router.post("", response_model=JobOut)
async def create_job(data: JobCreateIn, request: Request, db: AsyncSession = Depends(get_db)):
    user = require_auth(request)
    url = data.sourceUrl.strip() if data.sourceUrl else ""
    if not is_valid_status_url(url):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_url")

    canonical = normalize_url(url)

    job = Job(
        user_id=user.id,
        source_url=url,
        canonical_url=canonical,
        status=JobStatus.queued,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    event = JobEvent(job_id=job.id, event_type="created", message="Job created and queued")
    db.add(event)
    await db.commit()

    return job

@router.get("", response_model=JobListOut)
async def list_jobs(request: Request, db: AsyncSession = Depends(get_db), limit: int = 20, offset: int = 0):
    user = require_auth(request)
    stmt = select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    res = await db.execute(stmt)
    jobs = res.scalars().all()

    count_stmt = select(func.count()).select_from(Job).where(Job.user_id == user.id)
    count_res = await db.execute(count_stmt)
    total = count_res.scalar() or 0

    return {"jobs": jobs, "total": total}

@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = require_auth(request)
    stmt = select(Job).where(Job.id == uuid.UUID(job_id))
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job or str(job.user_id) != str(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

    ev_stmt = select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at.asc())
    ev_res = await db.execute(ev_stmt)
    events = ev_res.scalars().all()
    job.events = events
    return job

@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = require_auth(request)
    stmt = select(Job).where(Job.id == uuid.UUID(job_id))
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job or str(job.user_id) != str(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if job.status not in (JobStatus.failed,):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only_failed_jobs_can_be_retried")

    job.status = JobStatus.queued
    job.error_code = None
    job.error_message = None
    job.progress_stage = None
    job.progress_pct = None
    job.started_at = None
    job.completed_at = None
    await db.commit()
    await db.refresh(job)

    event = JobEvent(job_id=job.id, event_type="retried", message="Job retried and re-queued")
    db.add(event)
    await db.commit()

    return job

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = require_auth(request)
    stmt = select(Job).where(Job.id == uuid.UUID(job_id))
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job or str(job.user_id) != str(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if job.status not in (JobStatus.queued, JobStatus.failed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_delete_active_job")

    # Delete events first
    del_ev = select(JobEvent).where(JobEvent.job_id == job.id)
    del_ev_res = await db.execute(del_ev)
    for ev in del_ev_res.scalars().all():
        await db.delete(ev)
    await db.delete(job)
    await db.commit()
    return
