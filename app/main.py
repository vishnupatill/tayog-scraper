"""
Tayog Scraping Microservice - Main Application Entry Point
Production-grade FastAPI application with full lifecycle management
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import jobs, scrape, health
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.db.mongodb import connect_to_mongo, close_mongo_connection

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    logger.info("🚀 Starting Tayog Scraping Microservice...")
    await connect_to_mongo()
    logger.info("✅ MongoDB connection established")
    yield
    logger.info("🛑 Shutting down Tayog Scraping Microservice...")
    await close_mongo_connection()
    logger.info("✅ Cleanup complete")


app = FastAPI(
    title="Tayog Scraping Microservice",
    description="""
    Production-grade web scraping microservice for the Tayog platform.
    Scrapes job listings from multiple sources with async processing,
    deduplication, and structured data storage.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request with timing info."""
    start = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000
    logger.info(
        "%(method)s %(path)s → %(status)s (%(ms).1fms)",
        {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": duration,
        },
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler — never expose raw tracebacks."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error. Check logs."},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router, tags=["Health"])
app.include_router(scrape.router, prefix="/api/v1", tags=["Scraping"])
app.include_router(jobs.router, prefix="/api/v1", tags=["Jobs"])
