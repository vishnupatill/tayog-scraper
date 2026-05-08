"""
MongoDB async driver (Motor) — connection management + repository pattern.
All DB operations go through this layer; nothing talks to Mongo directly.
"""

import logging
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# Module-level client — shared across all requests
_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


# ── Connection lifecycle ───────────────────────────────────────────────────────

async def connect_to_mongo() -> None:
    global _client, _db
    _client = AsyncIOMotorClient(
        settings.MONGODB_URL,
        serverSelectionTimeoutMS=5_000,
        maxPoolSize=20,
        minPoolSize=2,
    )
    _db = _client[settings.MONGODB_DB]
    await _ensure_indexes()
    logger.info("MongoDB connected → db=%s", settings.MONGODB_DB)


async def close_mongo_connection() -> None:
    global _client
    if _client:
        _client.close()
        logger.info("MongoDB connection closed")


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialised. Call connect_to_mongo() first.")
    return _db


async def _ensure_indexes() -> None:
    """Create indexes on startup — idempotent."""
    db = get_db()

    jobs_col = db[settings.MONGODB_JOBS_COLLECTION]
    await jobs_col.create_indexes([
        IndexModel([("fingerprint", ASCENDING)], unique=True, sparse=True),
        IndexModel([("source", ASCENDING)]),
        IndexModel([("scraped_at", DESCENDING)]),
        IndexModel([("title", "text"), ("company", "text"), ("description", "text")]),
        IndexModel([("location", ASCENDING)]),
        IndexModel([("skills", ASCENDING)]),
        IndexModel([("is_active", ASCENDING)]),
    ])

    runs_col = db[settings.MONGODB_SCRAPE_RUNS_COLLECTION]
    await runs_col.create_indexes([
        IndexModel([("job_id", ASCENDING)], unique=True),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("started_at", DESCENDING)]),
    ])

    logger.info("MongoDB indexes ensured")


# ── Job Repository ─────────────────────────────────────────────────────────────

class JobRepository:
    def __init__(self):
        self._col = get_db()[settings.MONGODB_JOBS_COLLECTION]

    async def upsert(self, job: Dict[str, Any]) -> bool:
        """Insert or skip if fingerprint exists. Returns True if new."""
        fingerprint = job.get("fingerprint")
        if not fingerprint:
            await self._col.insert_one(job)
            return True

        result = await self._col.update_one(
            {"fingerprint": fingerprint},
            {"$setOnInsert": job},
            upsert=True,
        )
        return result.upserted_id is not None

    async def bulk_upsert(self, jobs: List[Dict[str, Any]]) -> Dict[str, int]:
        """Bulk upsert; returns counts of new vs duplicate."""
        new, dupes = 0, 0
        for job in jobs:
            inserted = await self.upsert(job)
            if inserted:
                new += 1
            else:
                dupes += 1
        return {"new": new, "duplicates": dupes}

    async def find_many(
        self,
        filters: Dict[str, Any] = None,
        skip: int = 0,
        limit: int = 20,
        sort_by: str = "scraped_at",
        sort_dir: int = DESCENDING,
    ) -> List[Dict[str, Any]]:
        query = filters or {}
        cursor = self._col.find(query, {"_id": 0}).sort(sort_by, sort_dir).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def count(self, filters: Dict[str, Any] = None) -> int:
        return await self._col.count_documents(filters or {})

    async def find_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._col.find_one({"id": job_id}, {"_id": 0})

    async def text_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        cursor = self._col.find(
            {"$text": {"$search": query}},
            {"score": {"$meta": "textScore"}, "_id": 0},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)
        return await cursor.to_list(length=limit)


# ── Scrape Run Repository ──────────────────────────────────────────────────────

class ScrapeRunRepository:
    def __init__(self):
        self._col = get_db()[settings.MONGODB_SCRAPE_RUNS_COLLECTION]

    async def create(self, run: Dict[str, Any]) -> str:
        await self._col.insert_one(run)
        return run["job_id"]

    async def update(self, job_id: str, updates: Dict[str, Any]) -> None:
        await self._col.update_one({"job_id": job_id}, {"$set": updates})

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self._col.find_one({"job_id": job_id}, {"_id": 0})

    async def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        cursor = self._col.find({}, {"_id": 0}).sort("started_at", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)
