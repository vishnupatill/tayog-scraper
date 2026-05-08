"""
GET /api/v1/jobs      — paginated job listings with filtering + full-text search
GET /api/v1/jobs/{id} — single job detail
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.models import JobListing, PaginatedJobsResponse
from app.db.mongodb import JobRepository

router = APIRouter()
logger = logging.getLogger(__name__)


def get_job_repo() -> JobRepository:
    return JobRepository()


@router.get(
    "/jobs",
    response_model=PaginatedJobsResponse,
    summary="List scraped job listings",
    description="""
    Returns paginated job listings. Supports filtering by source, location,
    skills, and full-text search across title, company, and description.
    """,
)
async def list_jobs(
    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Results per page"),
    # Filters
    source: Optional[str] = Query(None, description="Filter by source (linkedin, naukri, ...)"),
    location: Optional[str] = Query(None, description="Filter by location (partial match)"),
    job_type: Optional[str] = Query(None, description="Filter by job type"),
    skills: Optional[List[str]] = Query(None, description="Filter by skills (any match)"),
    company: Optional[str] = Query(None, description="Filter by company name (partial match)"),
    min_salary: Optional[float] = Query(None, description="Minimum salary filter"),
    # Search
    q: Optional[str] = Query(None, description="Full-text search query"),
    repo: JobRepository = Depends(get_job_repo),
):
    # Build MongoDB filter dict
    filters = {"is_active": True}

    if source:
        filters["source"] = source.lower()
    if location:
        filters["location"] = {"$regex": location, "$options": "i"}
    if job_type:
        filters["job_type"] = job_type
    if skills:
        filters["skills"] = {"$in": [s.lower() for s in skills]}
    if company:
        filters["company"] = {"$regex": company, "$options": "i"}
    if min_salary:
        filters["salary.min"] = {"$gte": min_salary}

    skip = (page - 1) * page_size

    if q:
        # Full-text search via MongoDB text index
        jobs = await repo.text_search(q, limit=page_size)
        total = len(jobs)
    else:
        total = await repo.count(filters)
        jobs = await repo.find_many(filters=filters, skip=skip, limit=page_size)

    total_pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedJobsResponse(
        success=True,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        data=[JobListing(**j) for j in jobs],
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobListing,
    summary="Get a single job by ID",
)
async def get_job(
    job_id: str,
    repo: JobRepository = Depends(get_job_repo),
):
    job = await repo.find_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobListing(**job)


@router.get(
    "/jobs/stats/summary",
    summary="Aggregate stats about stored jobs",
)
async def job_stats(repo: JobRepository = Depends(get_job_repo)):
    """Returns aggregate counts useful for dashboards."""
    total = await repo.count({"is_active": True})
    by_source = {}
    for src in ("linkedin", "naukri", "indeed", "glassdoor"):
        by_source[src] = await repo.count({"source": src, "is_active": True})

    return {
        "success": True,
        "total_jobs": total,
        "by_source": by_source,
    }
