import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.database import db
from src.routers import auth, jobs
from src.worker.scheduler import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "sessions"):
        app.state.sessions = {}
    start_scheduler()
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
    session_id = request.cookies.get("session_id")
    if session_id and hasattr(request.app.state, "sessions"):
        user_id = request.app.state.sessions.get(session_id)
        if user_id:
            user = db.get_user_by_id(user_id)
            if user:
                request.state.current_user = user
            else:
                request.app.state.sessions.pop(session_id, None)
    response = await call_next(request)
    return response


app.include_router(auth.router)
app.include_router(jobs.router)


@app.get("/health")
async def health():
    return {"status": "ok"}