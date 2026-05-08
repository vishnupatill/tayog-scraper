"""
Test suite — covers API contracts and data processing pipeline.
Run: pytest tests/ -v
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """Patch MongoDB so tests don't need a real connection."""
    with patch("app.db.mongodb._db", MagicMock()), \
         patch("app.db.mongodb._client", MagicMock()):
        yield


@pytest.fixture
async def async_client(mock_db):
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Health endpoint ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "tayog-scraper"


# ── Scrape endpoint ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_scrape_returns_202(async_client):
    mock_repo = AsyncMock()
    mock_repo.create = AsyncMock()

    with patch("app.api.routes.scrape.ScrapeRunRepository", return_value=mock_repo), \
         patch("app.tasks.scrape_tasks.run_scrape_job") as mock_task:
        mock_task.delay = MagicMock()

        resp = await async_client.post("/api/v1/scrape", json={
            "sources": ["linkedin"],
            "keywords": ["python developer"],
            "location": "Hyderabad",
            "max_pages": 2,
        })

    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert "job_id" in body
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_trigger_scrape_invalid_payload(async_client):
    resp = await async_client.post("/api/v1/scrape", json={
        "sources": [],
        "keywords": [],         # empty keywords should fail validation
        "location": "Delhi",
    })
    assert resp.status_code == 422


# ── Jobs endpoint ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_jobs_pagination(async_client):
    mock_repo = AsyncMock()
    mock_repo.count = AsyncMock(return_value=100)
    mock_repo.find_many = AsyncMock(return_value=[
        {
            "title": "Software Engineer",
            "company": "Acme Corp",
            "location": "Hyderabad",
            "job_type": "full_time",
            "source": "linkedin",
            "scraped_at": "2024-05-01T00:00:00",
            "is_active": True,
        }
    ])

    with patch("app.api.routes.jobs.JobRepository", return_value=mock_repo):
        resp = await async_client.get("/api/v1/jobs?page=1&page_size=20")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["total"] == 100
    assert body["total_pages"] == 5
    assert len(body["data"]) == 1


# ── Data Processor ─────────────────────────────────────────────────────────────

class TestJobDataProcessor:
    def setup_method(self):
        from app.scraper.processor import JobDataProcessor
        self.processor = JobDataProcessor()

    def test_processes_valid_job(self):
        raw = {
            "title": "  Senior Python Developer  ",
            "company": "TechCorp",
            "location": "Hyderabad, India",
            "description": "We need expertise in FastAPI, Docker, PostgreSQL",
            "salary_raw": "₹15 LPA – ₹25 LPA",
        }
        result = self.processor.process(raw, "linkedin")
        assert result is not None
        assert result["title"] == "Senior Python Developer"
        assert result["location"] == "Hyderabad"   # "India" stripped
        assert "fastapi" in result["skills"]
        assert "docker" in result["skills"]
        assert result["salary"]["min"] == 1_500_000
        assert result["salary"]["max"] == 2_500_000
        assert result["fingerprint"] is not None

    def test_drops_records_missing_title(self):
        raw = {"title": "", "company": "Acme", "location": "Delhi"}
        result = self.processor.process(raw, "naukri")
        assert result is None

    def test_drops_records_missing_company(self):
        raw = {"title": "Engineer", "company": "", "location": "Mumbai"}
        result = self.processor.process(raw, "naukri")
        assert result is None

    def test_deduplication_in_batch(self):
        raw = [
            {"title": "Dev", "company": "Acme", "location": "Delhi", "description": ""},
            {"title": "Dev", "company": "Acme", "location": "Delhi", "description": ""},  # dupe
            {"title": "QA",  "company": "Beta", "location": "Mumbai", "description": ""},
        ]
        results = self.processor.process_batch(raw, "linkedin")
        assert len(results) == 2   # one dupe skipped

    def test_fingerprint_consistency(self):
        from app.scraper.processor import _fingerprint
        fp1 = _fingerprint("Software Engineer", "Google", "Bangalore")
        fp2 = _fingerprint("Software Engineer", "Google", "Bangalore")
        assert fp1 == fp2  # deterministic

    def test_skill_extraction(self):
        from app.scraper.processor import _extract_skills
        text = "Looking for expertise in Python, FastAPI, Docker and Kubernetes"
        skills = _extract_skills(text)
        assert "python" in skills
        assert "fastapi" in skills
        assert "docker" in skills
        assert "kubernetes" in skills

    def test_salary_extraction_lpa(self):
        from app.scraper.processor import _extract_salary
        result = _extract_salary("₹8 LPA – ₹14 LPA")
        assert result["min"] == 800_000
        assert result["max"] == 1_400_000

    def test_salary_extraction_no_salary(self):
        from app.scraper.processor import _extract_salary
        result = _extract_salary(None)
        assert result is None

    def test_salary_extraction_raw_text(self):
        from app.scraper.processor import _extract_salary
        result = _extract_salary("Competitive salary")
        assert result["raw"] == "Competitive salary"
        assert result["min"] is None
