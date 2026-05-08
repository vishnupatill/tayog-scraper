"""
Central configuration — all settings pulled from environment variables.
Never hardcode secrets; use a .env file locally and secrets manager in prod.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "tayog-scraper"
    ENV: str = "development"          # development | staging | production
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # ── Server ───────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    ALLOWED_ORIGINS: List[str] = ["*"]
    ALLOWED_HOSTS: List[str] = ["*"]

    # ── MongoDB ───────────────────────────────────────────────────────────────
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "tayog_scraper"
    MONGODB_JOBS_COLLECTION: str = "jobs"
    MONGODB_SCRAPE_RUNS_COLLECTION: str = "scrape_runs"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # ── Scraper ───────────────────────────────────────────────────────────────
    SCRAPER_CONCURRENCY: int = 8
    SCRAPER_DELAY: float = 1.5        # seconds between requests
    SCRAPER_TIMEOUT: int = 30         # seconds per request
    MAX_RETRY_ATTEMPTS: int = 3
    RETRY_BACKOFF: float = 2.0        # exponential backoff multiplier

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_REQUESTS: int = 60
    RATE_LIMIT_WINDOW: int = 60       # seconds

    # ── Playwright ────────────────────────────────────────────────────────────
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_TIMEOUT: int = 30_000  # ms

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"          # json | text

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
