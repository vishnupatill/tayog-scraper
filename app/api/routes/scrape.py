"""
POST /api/v1/scrape — trigger a scraping job.
GET  /api/v1/scrape/{job_id} — poll job status.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.models import (
    ScrapeRequest,
    ScrapeResponse,
    ScrapeRunSummary,
    ScrapeStatus,
)
from app.db.mongodb import ScrapeRunRepository

router = APIRouter()
logger = logging.getLogger(__name__)


def get_run_repo() -> ScrapeRunRepository:
    return ScrapeRunRepository()


@router.post(
    "/scrape",
    response_model=ScrapeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a new scraping job",
    description="""
    Enqueues an async scraping job. Returns immediately with a `job_id` that
    can be polled via GET /api/v1/scrape/{job_id}.
    """,
)
async def trigger_scrape(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks,
    repo: ScrapeRunRepository = Depends(get_run_repo),
):
    job_id = str(uuid.uuid4())
    sources_list = [s.value for s in request.sources]

    # Persist the run record immediately so it's pollable
    await repo.create({
        "job_id": job_id,
        "status": ScrapeStatus.PENDING.value,
        "sources": sources_list,
        "keywords": request.keywords,
        "location": request.location,
        "max_pages": request.max_pages,
        "filters": request.filters,
        "triggered_by": "api",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Dispatch to Celery
    try:
        from app.tasks.scrape_tasks import run_scrape_job
        run_scrape_job.delay(
            job_id=job_id,
            sources=sources_list,
            keywords=request.keywords,
            location=request.location,
            max_pages=request.max_pages,
            filters=request.filters,
        )
        logger.info("Scrape job %s dispatched to Celery", job_id)
    except Exception as exc:
        # Celery unavailable — fall back to FastAPI BackgroundTasks (dev mode)
        logger.warning("Celery unavailable (%s); using background task", exc)
        background_tasks.add_task(
            _run_scrape_background,
            job_id, sources_list, request.keywords, request.location,
            request.max_pages, request.filters,
        )

    return ScrapeResponse(
        success=True,
        job_id=job_id,
        status=ScrapeStatus.PENDING,
        message="Scraping job enqueued. Poll /api/v1/scrape/{job_id} for status.",
        estimated_duration_seconds=len(sources_list) * len(request.keywords) * request.max_pages * 3,
    )


@router.get(
    "/scrape/{job_id}",
    response_model=ScrapeRunSummary,
    summary="Poll scrape job status",
)
async def get_scrape_status(
    job_id: str,
    repo: ScrapeRunRepository = Depends(get_run_repo),
):
    run = await repo.get(job_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return ScrapeRunSummary(**run)


@router.get(
    "/scrape",
    summary="List recent scrape runs",
)
async def list_scrape_runs(
    limit: int = 20,
    repo: ScrapeRunRepository = Depends(get_run_repo),
):
    runs = await repo.list_recent(limit=limit)
    return {"success": True, "total": len(runs), "data": runs}


# ── Background task fallback (no Celery) ──────────────────────────────────────

async def _run_scrape_background(job_id, sources, keywords, location, max_pages, filters):
    from app.db.mongodb import JobRepository, ScrapeRunRepository
    from app.scraper.engine import ScraperEngine
    from app.scraper.processor import JobDataProcessor

    run_repo = ScrapeRunRepository()
    job_repo = JobRepository()
    engine = ScraperEngine()
    processor = JobDataProcessor()

    started_at = datetime.utcnow()
    await run_repo.update(job_id, {
        "status": ScrapeStatus.RUNNING.value,
        "started_at": started_at.isoformat(),
    })

    total_found = new_jobs = dupes = errors = 0

    for source in sources:
        for keyword in keywords:
            try:
                raw = await engine.scrape(source=source, keyword=keyword,
                                          location=location, max_pages=max_pages)
                total_found += len(raw)
                clean = processor.process_batch(raw, source)
                counts = await job_repo.bulk_upsert(clean)
                new_jobs += counts["new"]
                dupes += counts["duplicates"]
            except Exception as exc:
                errors += 1
                logger.exception("Background scrape error %s/%s: %s", source, keyword, exc)

    completed_at = datetime.utcnow()
    await run_repo.update(job_id, {
        "status": ScrapeStatus.COMPLETED.value,
        "completed_at": completed_at.isoformat(),
        "duration_seconds": (completed_at - started_at).total_seconds(),
        "total_found": total_found,
        "new_jobs": new_jobs,
        "duplicates_skipped": dupes,
        "errors": errors,
    })
