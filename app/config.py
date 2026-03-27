from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "MedTalk")
    database_path: Path = Path(os.getenv("DATABASE_PATH", BASE_DIR / "medtalk.db"))
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")
    auth_secret_key: str = os.getenv("AUTH_SECRET_KEY", "change-this-in-env")
    auth_cookie_name: str = os.getenv("AUTH_COOKIE_NAME", "medtalk_session")
    auth_cookie_secure: bool = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"


settings = Settings()
