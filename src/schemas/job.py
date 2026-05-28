from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Any
from uuid import UUID

class JobEventOut(BaseModel):
    id: UUID
    job_id: UUID
    event_type: str
    message: str
    metadata_json: Optional[Any] = None
    created_at: datetime

    class Config:
        from_attributes = True

class JobOut(BaseModel):
    id: UUID
    user_id: UUID
    source_url: str
    canonical_url: str
    status: str
    progress_stage: Optional[str] = None
    progress_pct: Optional[int] = None
    source_title: Optional[str] = None
    source_duration: Optional[int] = None
    download_path: Optional[str] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime
    events: Optional[List[JobEventOut]] = None

    class Config:
        from_attributes = True

class JobListOut(BaseModel):
    jobs: List[JobOut]
    total: int

class JobCreateIn(BaseModel):
    sourceUrl: str

class ErrorOut(BaseModel):
    detail: str
