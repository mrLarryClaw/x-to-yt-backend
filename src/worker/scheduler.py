import os
import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.database import db, Job
from src.services.google_auth import refresh_if_needed
from src.services.youtube import upload_video

DOWNLOAD_DIR = "/tmp/x_to_yt_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

scheduler = None


def start_scheduler():
    global scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(worker_tick, "interval", seconds=30, max_instances=1)
    scheduler.start()


async def worker_tick():
    # Find oldest queued job
    all_jobs = list(db._jobs.values())
    queued = [j for j in all_jobs if j.status == "queued"]
    if not queued:
        return
    queued.sort(key=lambda j: j.created_at)
    job = queued[0]

    job.status = "downloading"
    job.started_at = datetime.now(timezone.utc)
    job.progress_stage = "downloading"
    db.update_job(job)

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

        actual_path = temp_path
        if not os.path.exists(actual_path):
            for ext in (".mp4", ".webm", ".mkv"):
                candidate = temp_path + ext
                if os.path.exists(candidate):
                    actual_path = candidate
                    break

        if not os.path.exists(actual_path):
            raise RuntimeError("yt-dlp did not produce an output file")

        job.status = "uploading"
        job.download_path = actual_path
        job.progress_stage = "uploading"
        db.update_job(job)
    except Exception as exc:
        job.status = "failed"
        job.error_code = "download_failed"
        job.error_message = str(exc)[:500]
        job.updated_at = datetime.now(timezone.utc)
        db.update_job(job)
        return

    # ── Upload ──
    try:
        token = db.get_token_by_user(job.user_id)
        if not token:
            raise RuntimeError("No YouTube tokens found for user")

        access_token = await refresh_if_needed(token)

        video_id = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: upload_video(
                access_token,
                job.download_path,
                title=job.source_title or None,
                description="Uploaded from X via x-to-yt",
                privacy_status="private",
            ),
        )

        job.status = "completed"
        job.youtube_video_id = video_id
        job.youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        job.progress_stage = "completed"
        job.progress_pct = 100
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        db.update_job(job)

        # cleanup temp file
        try:
            if job.download_path and os.path.exists(job.download_path):
                os.remove(job.download_path)
        except Exception:
            pass
    except Exception as exc:
        job.status = "failed"
        job.error_code = "upload_failed"
        job.error_message = str(exc)[:500]
        job.updated_at = datetime.now(timezone.utc)
        db.update_job(job)


def shutdown_scheduler():
    if scheduler:
        scheduler.shutdown(wait=False)