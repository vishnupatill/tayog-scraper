"""
Celery tasks — async scraping jobs dispatched by the API.
Each task manages its own DB session (Motor inside asyncio event loop).
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import List

from celery import Task

from app.core.config import settings
from app.core.models import ScrapeStatus
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro):
    """Run an async coroutine inside a Celery (sync) task safely."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ScrapeTask(Task):
    """Base task class with shared retry configuration."""
    abstract = True
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 600        # max 10 min wait
    retry_jitter = True
    max_retries = settings.MAX_RETRY_ATTEMPTS


@celery_app.task(bind=True, base=ScrapeTask, name="app.tasks.scrape_tasks.run_scrape_job")
def run_scrape_job(
    self,
    job_id: str,
    sources: List[str],
    keywords: List[str],
    location: str,
    max_pages: int = 5,
    filters: dict = None,
):
    """
    Main scraping task.
    1. Updates run status → RUNNING
    2. Calls scraper engine for each source
    3. Processes + stores results
    4. Updates run status → COMPLETED / FAILED
    """
    return run_async(
        _execute_scrape(self, job_id, sources, keywords, location, max_pages, filters or {})
    )


async def _execute_scrape(task, job_id, sources, keywords, location, max_pages, filters):
    from app.db.mongodb import connect_to_mongo, ScrapeRunRepository, JobRepository
    from app.scraper.engine import ScraperEngine
    from app.scraper.processor import JobDataProcessor

    # Lazy-connect for this worker process
    await connect_to_mongo()

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

    try:
        for source in sources:
            for keyword in keywords:
                logger.info("Scraping source=%s keyword=%s location=%s", source, keyword, location)
                try:
                    raw_items = await engine.scrape(
                        source=source,
                        keyword=keyword,
                        location=location,
                        max_pages=max_pages,
                    )
                    total_found += len(raw_items)
                    clean_jobs = processor.process_batch(raw_items, source)
                    counts = await job_repo.bulk_upsert(clean_jobs)
                    new_jobs += counts["new"]
                    dupes += counts["duplicates"]
                except Exception as exc:
                    errors += 1
                    logger.exception("Error scraping %s/%s: %s", source, keyword, exc)

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

        logger.info(
            "Scrape job %s complete — found=%d new=%d dupes=%d errors=%d",
            job_id, total_found, new_jobs, dupes, errors,
        )
        return {"job_id": job_id, "new_jobs": new_jobs, "total": total_found}

    except Exception as exc:
        await run_repo.update(job_id, {
            "status": ScrapeStatus.FAILED.value,
            "error_message": str(exc),
        })
        raise


@celery_app.task(name="app.tasks.scrape_tasks.run_scheduled_scrape")
def run_scheduled_scrape(sources: List[str], keywords: List[str], location: str):
    """Celery Beat periodic task — auto-triggered every 6 hours."""
    job_id = str(uuid.uuid4())
    logger.info("Scheduled scrape triggered — job_id=%s", job_id)

    run_async(_create_run_record(job_id, sources, keywords, location))
    run_scrape_job.delay(job_id, sources, keywords, location)
    return job_id


async def _create_run_record(job_id, sources, keywords, location):
    from app.db.mongodb import connect_to_mongo, ScrapeRunRepository
    await connect_to_mongo()
    repo = ScrapeRunRepository()
    await repo.create({
        "job_id": job_id,
        "status": ScrapeStatus.PENDING.value,
        "sources": sources,
        "keywords": keywords,
        "location": location,
        "triggered_by": "scheduler",
        "created_at": datetime.utcnow().isoformat(),
    })
