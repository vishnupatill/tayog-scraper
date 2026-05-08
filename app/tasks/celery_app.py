"""
Celery application — async task queue backed by Redis.
Tasks defined here are triggered by API endpoints and run in background workers.
"""

import logging
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "tayog_scraper",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.scrape_tasks"],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    
    # Default queue configuration
    task_default_queue="default", 
    task_default_exchange="default", 
    task_default_routing_key="default",

    # Reliability
    task_acks_late=True,                    # ack after execution, not before
    task_reject_on_worker_lost=True,        # requeue if worker dies mid-task
    worker_prefetch_multiplier=1,           # one task at a time per worker slot
    task_max_retries=settings.MAX_RETRY_ATTEMPTS,

    # Results
    result_expires=86_400,                  # 24 hours
    task_track_started=True,

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Beat schedule — automated periodic scrapes
    beat_schedule={
        "scrape-jobs-every-6h": {
            "task": "app.tasks.scrape_tasks.run_scheduled_scrape",
            "schedule": crontab(minute=0, hour="*/6"),
            "args": (["linkedin", "naukri"], ["software engineer", "data engineer"], "Hyderabad"),
        },
    },
)
