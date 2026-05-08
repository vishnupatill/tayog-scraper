"""Health and readiness endpoints — used by load balancers and k8s probes."""

import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db.mongodb import get_db

router = APIRouter()
logger = logging.getLogger(__name__)
_START_TIME = datetime.utcnow()


@router.get("/health", summary="Liveness probe")
async def health():
    """Always returns 200 if the process is alive."""
    return {"status": "ok", "service": "tayog-scraper", "timestamp": datetime.utcnow().isoformat()}


@router.get("/ready", summary="Readiness probe")
async def ready():
    """Returns 200 only if the service can handle traffic (DB reachable)."""
    try:
        db = get_db()
        await db.command("ping")
        uptime = (datetime.utcnow() - _START_TIME).total_seconds()
        return {
            "status": "ready",
            "uptime_seconds": uptime,
            "database": "connected",
        }
    except Exception as exc:
        logger.error("Readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unreachable", "error": str(exc)},
        )
