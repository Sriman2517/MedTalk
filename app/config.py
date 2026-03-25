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


settings = Settings()
