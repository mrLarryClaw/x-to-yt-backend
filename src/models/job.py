import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from src.database import Base

class JobStatus(str, enum.Enum):
    queued = "queued"
    downloading = "downloading"
    uploading = "uploading"
    completed = "completed"
    failed = "failed"

class Job(Base):
    __tablename__ = "jobs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_url = Column(String, nullable=False)
    canonical_url = Column(String, nullable=False)
    status = Column(Enum(JobStatus), default=JobStatus.queued, nullable=False)
    progress_stage = Column(String, nullable=True)
    progress_pct = Column(Integer, nullable=True)
    source_title = Column(String, nullable=True)
    source_duration = Column(Integer, nullable=True)
    download_path = Column(String, nullable=True)
    youtube_video_id = Column(String, nullable=True)
    youtube_url = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
