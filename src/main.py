import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.database import engine, Base, AsyncSessionLocal
from src.routers import auth, jobs
from src.worker.scheduler import start_scheduler, shutdown_scheduler
from sqlalchemy import select
from src.models import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if not hasattr(app.state, "sessions"):
        app.state.sessions = {}
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="X-to-YouTube Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
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
            try:
                async with AsyncSessionLocal() as db:
                    stmt = select(User).where(User.id == uuid.UUID(user_id))
                    res = await db.execute(stmt)
                    user = res.scalars().first()
                    request.state.current_user = user
                    if not user:
                        request.app.state.sessions.pop(session_id, None)
            except Exception:
                pass
    response = await call_next(request)
    return response


app.include_router(auth.router)
app.include_router(jobs.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
