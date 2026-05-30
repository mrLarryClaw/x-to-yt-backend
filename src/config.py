from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    allowed_emails: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    redirect_uri: str = ""
    frontend_url: str = ""
    database_url: str = ""
    secret_key: str = ""
    session_secret: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def allowed_emails_list(self) -> List[str]:
        return [e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()]

    @property
    def psycopg_url(self) -> str:
        """DATABASE_URL normalized for psycopg2 (strip async/pg8000 drivers)."""
        url = self.database_url
        # postgresql+asyncpg://... > postgresql://...
        # postgresql+psycopg://... > postgresql://...
        return url.replace("+asyncpg", "").replace("+psycopg", "").replace("+psycopg2", "")

settings = Settings()
