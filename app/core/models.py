"""
Domain models — single source of truth for all data shapes.
Used by API layer, scraper engine, and DB layer.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class JobType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class ScrapeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScrapeSource(str, Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    NAUKRI = "naukri"
    GLASSDOOR = "glassdoor"
    CUSTOM = "custom"


# ── Job Models ─────────────────────────────────────────────────────────────────

class SalaryInfo(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    currency: str = "INR"
    period: str = "yearly"   # yearly | monthly | hourly
    raw: Optional[str] = None


class JobListing(BaseModel):
    """Canonical job listing — the core domain entity."""
    id: Optional[str] = Field(None, alias="_id")
    title: str
    company: str
    location: str
    job_type: JobType = JobType.UNKNOWN
    salary: Optional[SalaryInfo] = None
    skills: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    url: Optional[str] = None
    source: ScrapeSource = ScrapeSource.CUSTOM
    posted_at: Optional[datetime] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    fingerprint: Optional[str] = None   # SHA-256 for dedup
    is_active: bool = True

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}


# ── Scrape Run Models ──────────────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    """Payload Tayog sends to POST /api/v1/scrape."""
    sources: List[ScrapeSource] = Field(
        default=[ScrapeSource.LINKEDIN],
        description="Which job boards to scrape",
    )
    keywords: List[str] = Field(
        default=["software engineer"],
        description="Search keywords / job titles",
    )
    location: str = Field(default="Hyderabad", description="Target location")
    max_pages: int = Field(default=5, ge=1, le=50, description="Max pages per source")
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional extra filters (experience, salary, etc.)",
    )

    @validator("keywords")
    def keywords_not_empty(cls, v):
        if not v:
            raise ValueError("Provide at least one keyword")
        return [kw.strip() for kw in v if kw.strip()]


class ScrapeResponse(BaseModel):
    """Immediate response after triggering a scrape."""
    success: bool
    job_id: str
    status: ScrapeStatus
    message: str
    estimated_duration_seconds: Optional[int] = None


class ScrapeRunSummary(BaseModel):
    """Summary of a completed scrape run."""
    job_id: str
    status: ScrapeStatus
    sources: List[str]
    keywords: List[str]
    total_found: int = 0
    new_jobs: int = 0
    duplicates_skipped: int = 0
    errors: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


# ── API Response Wrappers ──────────────────────────────────────────────────────

class PaginatedJobsResponse(BaseModel):
    success: bool = True
    total: int
    page: int
    page_size: int
    total_pages: int
    data: List[JobListing]


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
