import os
import asyncio
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.models import Job, JobEvent, JobStatus, OAuthToken
from src.services.crypto import crypto_service
from src.services.google_auth import refresh_if_needed
from src.services.youtube import upload_video

DOWNLOAD_DIR = "/tmp/x_to_yt_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

scheduler: Optional[AsyncIOScheduler] = None


def start_scheduler():
    global scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(worker_tick, "interval", seconds=30, max_instances=1)
    scheduler.start()


async def worker_tick():
    await _process_one_job()


async def _process_one_job():
    db = AsyncSessionLocal()
    try:
        stmt = (
            select(Job)
            .where(Job.status == JobStatus.queued)
            .order_by(Job.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        res = await db.execute(stmt)
        job = res.scalars().first()
        if not job:
            return

        job.status = JobStatus.downloading
        job.started_at = datetime.now(timezone.utc)
        db.add(
            JobEvent(job_id=job.id, event_type="stage_change", message="Worker started downloading")
        )
        await db.commit()
        await db.refresh(job)

        temp_path = os.path.join(DOWNLOAD_DIR, f"{job.id}.mp4")

        # ── Download ──
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "--no-playlist",
                "-f", "best[ext=mp4]/best",
                "-o", temp_path,
                job.canonical_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode("utf-8", errors="replace")[:500])

            # Find actual output file if yt-dlp appended extension
            actual_path = temp_path
            if not os.path.exists(actual_path):
                for ext in (".mp4", ".webm", ".mkv"):
                    candidate = temp_path + ext
                    if os.path.exists(candidate):
                        actual_path = candidate
                        break

            if not os.path.exists(actual_path):
                raise RuntimeError("yt-dlp did not produce an output file")

            job.status = JobStatus.uploading
            job.download_path = actual_path
            job.progress_stage = "uploading"
            db.add(
                JobEvent(job_id=job.id, event_type="stage_change", message="Download succeeded, now uploading")
            )
            await db.commit()
            await db.refresh(job)
        except Exception as exc:
            job.status = JobStatus.failed
            job.error_code = "download_failed"
            job.error_message = str(exc)[:500]
            db.add(
                JobEvent(job_id=job.id, event_type="error", message=f"Download failed: {job.error_message}")
            )
            await db.commit()
            return

        # ── Upload ──
        try:
            stmt_token = select(OAuthToken).where(OAuthToken.user_id == job.user_id)
            token_res = await db.execute(stmt_token)
            token_row = token_res.scalars().first()
            if not token_row:
                raise RuntimeError("No YouTube tokens found for user")

            access_decrypted = await refresh_if_needed(db, token_row)

            video_id = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: upload_video(
                    access_decrypted,
                    job.download_path,
                    title=job.source_title or None,
                    description="Uploaded from X via x-to-yt",
                    privacy_status="private",
                ),
            )

            job.status = JobStatus.completed
            job.youtube_video_id = video_id
            job.youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            job.progress_stage = "completed"
            job.progress_pct = 100
            job.completed_at = datetime.now(timezone.utc)
            db.add(
                JobEvent(job_id=job.id, event_type="completed", message=f"Upload succeeded: {video_id}")
            )
            await db.commit()

            # cleanup temp file
            try:
                if job.download_path and os.path.exists(job.download_path):
                    os.remove(job.download_path)
                    job.download_path = None
                    await db.commit()
            except Exception:
                pass
        except Exception as exc:
            job.status = JobStatus.failed
            job.error_code = "upload_failed"
            job.error_message = str(exc)[:500]
            db.add(
                JobEvent(job_id=job.id, event_type="error", message=f"Upload failed: {job.error_message}")
            )
            await db.commit()
    finally:
        await db.close()


def shutdown_scheduler():
    if scheduler:
        scheduler.shutdown(wait=False)
