from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from uuid import UUID

class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    avatar_url: Optional[str] = None
    is_allowed: bool
    email_verified: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
