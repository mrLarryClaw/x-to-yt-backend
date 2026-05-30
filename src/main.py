import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.database import db
from src.worker.scheduler import start_scheduler, shutdown_scheduler

# Ensure all loggers propagate to root so Railway captures output
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("worker").setLevel(logging.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Starting worker scheduler...")
    start_scheduler()
    logging.info("Worker scheduler started (30s tick interval)")
    yield
    shutdown_scheduler()


app = FastAPI(title="X-to-YouTube Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url or "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    request.state.current_user = None
    # Check header first (cross-domain auth), then cookie
    auth_header = request.headers.get("Authorization", "")
    session_id = None
    if auth_header.startswith("Bearer "):
        session_id = auth_header[7:].strip()
    if not session_id:
        session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = request.headers.get("X-Session-Id")
    if session_id:
        user_id = db.get_session_user_id(session_id)
        if user_id:
            user = db.get_user_by_id(user_id)
            if user:
                request.state.current_user = user
    response = await call_next(request)
    return response


app.include_router(auth.router)
app.include_router(jobs.router)


@app.get("/health")
async def health():
    return {"status": "ok"}