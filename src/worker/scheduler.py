import os
import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.database import db, Job
from src.services.google_auth import refresh_if_needed
from src.services.youtube import upload_video

log = logging.getLogger("worker")
log.setLevel(logging.DEBUG)

DOWNLOAD_DIR = "/tmp/x_to_yt_downloads"
COOKIES_PATH = "/tmp/x_cookies.txt"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def _write_cookies_file():
    """Write X/Twitter cookies from env var to file for yt-dlp."""
    cookie_text = os.environ.get("X_COOKIES", "").strip()
    if not cookie_text:
        return None
    # Support both raw Netscape cookie text and base64-encoded
    import base64
    try:
        decoded = base64.b64decode(cookie_text).decode("utf-8", errors="replace")
        if "#HttpOnly_" in decoded or ".twitter.com" in decoded or ".x.com" in decoded:
            cookie_text = decoded
    except Exception:
        pass  # Not base64, treat as raw text
    with open(COOKIES_PATH, "w") as f:
        f.write(cookie_text)
    return COOKIES_PATH

scheduler = None


def start_scheduler():
    global scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(worker_tick, "interval", seconds=30, max_instances=1)
    scheduler.start()


async def worker_tick():
    log.info(f"Worker tick: checking for queued jobs...")
    # Find oldest queued job
    all_jobs = list(db._jobs.values())
    queued = [j for j in all_jobs if j.status == "queued"]
    if not queued:
        return
    log.info(f"Found {len(queued)} queued job(s)")
    queued.sort(key=lambda j: j.created_at)
    job = queued[0]

    job.status = "downloading"
    job.started_at = datetime.now(timezone.utc)
    job.progress_stage = "downloading"
    db.update_job(job)
    log.info(f"Processing job {job.id}: downloading {job.canonical_url}")
    print(f"WORKER: downloading job={job.id} url={job.canonical_url}", flush=True)

    temp_path = os.path.join(DOWNLOAD_DIR, f"{job.id}.mp4")

    # ── Download ──
    try:
        ytdlp_args = [
            "yt-dlp",
            "--no-playlist",
            "-f", "best[ext=mp4]/best",
            "-o", temp_path,
        ]
        cookies_path = _write_cookies_file()
        if cookies_path and os.path.exists(cookies_path):
            ytdlp_args.extend(["--cookies", cookies_path])
            print(f"WORKER: using X cookies for job={job.id}", flush=True)
        else:
            print(f"WORKER: WARNING - no X cookies configured, download may fail", flush=True)
        ytdlp_args.append(job.canonical_url)

        proc = await asyncio.create_subprocess_exec(
            *ytdlp_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")[:500]
            log.error(f"yt-dlp failed for job {job.id}: {err_text}")
            raise RuntimeError(err_text)

        actual_path = temp_path
        if not os.path.exists(actual_path):
            for ext in (".mp4", ".webm", ".mkv"):
                candidate = temp_path + ext
                if os.path.exists(candidate):
                    actual_path = candidate
                    break

        if not os.path.exists(actual_path):
            log.error(f"yt-dlp did not produce an output file for job {job.id}")
            raise RuntimeError("yt-dlp did not produce an output file")

        job.status = "uploading"
        job.download_path = actual_path
        job.progress_stage = "uploading"
        db.update_job(job)
        print(f"WORKER: download complete job={job.id} file={actual_path}", flush=True)
    except Exception as exc:
        log.error(f"Download failed for job {job.id}: {exc}")
        print(f"DOWNLOAD FAILED job={job.id} error={exc}", flush=True)
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
            log.error(f"No YouTube tokens found for user {job.user_id} in job {job.id}")
            raise RuntimeError("No YouTube tokens found for user")

        access_token = await refresh_if_needed(token)
        log.info(f"Uploading job {job.id} to YouTube")
        print(f"WORKER: uploading job={job.id} to YouTube", flush=True)

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
        print(f"WORKER: upload complete job={job.id} video_id={video_id}", flush=True)

        # cleanup temp file
        try:
            if job.download_path and os.path.exists(job.download_path):
                os.remove(job.download_path)
        except Exception:
            pass
    except Exception as exc:
        log.error(f"Upload failed for job {job.id}: {exc}")
        print(f"UPLOAD FAILED job={job.id} error={exc}", flush=True)
        job.status = "failed"
        job.error_code = "upload_failed"
        job.error_message = str(exc)[:500]
        job.updated_at = datetime.now(timezone.utc)
        db.update_job(job)


def shutdown_scheduler():
    if scheduler:
        scheduler.shutdown(wait=False)