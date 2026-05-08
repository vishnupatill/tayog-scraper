# Tayog Scraping Microservice

> Production-grade web scraping microservice built for the **Tayog platform**.  
> FastAPI · Celery · MongoDB · Redis · Docker · Playwright

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Quick Start (Local)](#quick-start-local)
4. [API Reference](#api-reference)
5. [Tayog Integration Guide](#tayog-integration-guide)
6. [Deployment](#deployment)
7. [Configuration](#configuration)
8. [Monitoring](#monitoring)
9. [Best Practices](#best-practices)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TAYOG PLATFORM                               │
│              (Frontend / Backend / Mobile App)                      │
└────────────────────────────┬────────────────────────────────────────┘
                             │  HTTPS REST calls
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    NGINX / API GATEWAY                              │
│              Rate Limiting · SSL Termination · Auth                 │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    FASTAPI MICROSERVICE                              │
│                                                                     │
│   POST /api/v1/scrape  →  trigger scraping job                     │
│   GET  /api/v1/scrape/{id} →  poll job status                      │
│   GET  /api/v1/jobs    →  paginated, filtered job listings         │
│   GET  /health · /ready  →  k8s / load balancer probes            │
│                                                                     │
│   ┌─────────────────┐    ┌──────────────────────────────────────┐  │
│   │  API Layer      │    │  Background Job (Celery task)        │  │
│   │  (routes/)      │───▶│  1. Dispatch to scraper engine       │  │
│   └─────────────────┘    │  2. Await raw results                │  │
│                          │  3. Run through data processor       │  │
│                          │  4. Bulk upsert to MongoDB           │  │
│                          └──────────────────────────────────────┘  │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │                                      │
           ▼                                      ▼
┌─────────────────────┐              ┌────────────────────────────────┐
│      REDIS          │              │      SCRAPER ENGINE            │
│  Celery Broker      │              │                                │
│  Result Backend     │   triggers   │  ┌──────────────────────────┐ │
│  Rate Limit Store   │─────────────▶│  │  LinkedInScraper (HTTP)  │ │
└─────────────────────┘              │  │  NaukriScraper (JSON API)│ │
                                     │  │  PlaywrightScraper (JS)  │ │
┌─────────────────────┐              │  └──────────────────────────┘ │
│   CELERY BEAT       │              │                                │
│  Scheduler          │              │  Middlewares:                  │
│  Every 6 hours      │              │  • User-agent rotation         │
│  auto-trigger       │              │  • Rate limiting per domain    │
└─────────────────────┘              │  • Retry w/ exponential backoff│
                                     └──────────────┬─────────────────┘
┌─────────────────────┐                             │
│      FLOWER         │                             ▼
│  Celery Monitor UI  │              ┌────────────────────────────────┐
│  :5555              │              │    DATA PROCESSOR              │
└─────────────────────┘              │                                │
                                     │  • Clean & normalize text      │
┌─────────────────────┐              │  • Extract salary ranges       │
│      MONGODB        │◀─────────────│  • Detect skills from taxonomy │
│  jobs collection    │  bulk upsert │  • SHA-256 fingerprint (dedup) │
│  scrape_runs col.   │              │  • Parse dates, cap description│
│  Text + geo indexes │              └────────────────────────────────┘
└─────────────────────┘
```

---

## Project Structure

```
tayog-scraper/
├── app/
│   ├── main.py                  # FastAPI app + middleware + routers
│   ├── core/
│   │   ├── config.py            # All settings (env-driven, pydantic-settings)
│   │   ├── models.py            # Domain models (JobListing, ScrapeRequest, ...)
│   │   └── logging_config.py   # Structured JSON logging
│   ├── api/
│   │   └── routes/
│   │       ├── health.py        # GET /health, GET /ready
│   │       ├── scrape.py        # POST /scrape, GET /scrape/{id}
│   │       └── jobs.py          # GET /jobs, GET /jobs/{id}
│   ├── db/
│   │   └── mongodb.py           # Motor async client + repositories
│   ├── scraper/
│   │   ├── engine.py            # ScraperEngine façade + spider implementations
│   │   └── processor.py        # Clean · normalize · fingerprint
│   └── tasks/
│       ├── celery_app.py        # Celery config + Beat schedule
│       └── scrape_tasks.py      # Celery tasks
├── docker/
│   └── mongo-init.js            # MongoDB initial setup script
├── tests/
│   ├── test_api.py
│   ├── test_processor.py
│   └── conftest.py
├── Dockerfile                   # Multi-stage production image
├── docker-compose.yml           # Full stack: api + worker + beat + mongo + redis
├── requirements.txt
├── .env.example
└── README.md
```

---

## Quick Start (Local)

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for local dev without Docker)

### 1. Clone and configure

```bash
git clone https://github.com/yourorg/tayog-scraper.git
cd tayog-scraper
cp .env.example .env
# Edit .env with your values
```

### 2. Run with Docker Compose

```bash
docker-compose up --build -d
```

This starts:
| Service    | URL                              | Purpose                  |
|------------|----------------------------------|--------------------------|
| API        | http://localhost:8000            | FastAPI REST endpoints   |
| Swagger UI | http://localhost:8000/docs       | Interactive API docs     |
| Flower     | http://localhost:5555            | Celery task monitor      |
| MongoDB    | mongodb://localhost:27017        | Primary database         |
| Redis      | redis://localhost:6379           | Task broker              |

### 3. Trigger your first scrape

```bash
curl -X POST http://localhost:8000/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "sources": ["linkedin", "naukri"],
    "keywords": ["software engineer", "data engineer"],
    "location": "Hyderabad",
    "max_pages": 3
  }'
```

Response:
```json
{
  "success": true,
  "job_id": "f3a2c1d0-...",
  "status": "pending",
  "message": "Scraping job enqueued. Poll /api/v1/scrape/{job_id} for status.",
  "estimated_duration_seconds": 54
}
```

### 4. Poll for status

```bash
curl http://localhost:8000/api/v1/scrape/f3a2c1d0-...
```

### 5. Fetch results

```bash
curl "http://localhost:8000/api/v1/jobs?location=Hyderabad&skills=python&page=1&page_size=20"
```

---

## API Reference

### POST /api/v1/scrape — Trigger scrape

**Request body:**
```json
{
  "sources": ["linkedin", "naukri"],
  "keywords": ["backend engineer"],
  "location": "Bangalore",
  "max_pages": 5,
  "filters": {}
}
```

**Response 202:**
```json
{
  "success": true,
  "job_id": "uuid",
  "status": "pending",
  "message": "...",
  "estimated_duration_seconds": 90
}
```

---

### GET /api/v1/scrape/{job_id} — Poll status

**Response 200:**
```json
{
  "job_id": "uuid",
  "status": "completed",
  "total_found": 142,
  "new_jobs": 98,
  "duplicates_skipped": 44,
  "errors": 0,
  "duration_seconds": 47.3
}
```

---

### GET /api/v1/jobs — List jobs

Query params:
| Param        | Type     | Description                              |
|--------------|----------|------------------------------------------|
| `page`       | int      | Page number (default: 1)                 |
| `page_size`  | int      | Results per page (default: 20, max: 100) |
| `source`     | string   | Filter by source (linkedin, naukri, ...) |
| `location`   | string   | Partial match on location                |
| `skills`     | string[] | Any-match on skills list                 |
| `company`    | string   | Partial match on company name            |
| `min_salary` | float    | Minimum salary filter                    |
| `q`          | string   | Full-text search                         |

---

## Tayog Integration Guide

```
Tayog Backend (Node.js / Python)
         │
         │  Step 1: POST /api/v1/scrape
         │  {sources, keywords, location, max_pages}
         ▼
Scraping Microservice ──── returns {job_id, status: "pending"}
         │
         │  Step 2: Poll GET /api/v1/scrape/{job_id}
         │  until status == "completed"
         ▼
         │  Step 3: GET /api/v1/jobs?location=X&skills=python
         │  Paginate through results for your UI
         ▼
Tayog Frontend displays job cards
```

### Example Node.js integration

```javascript
// 1. Trigger scrape
const triggerResponse = await fetch('https://scraper.tayog.com/api/v1/scrape', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    sources: ['linkedin', 'naukri'],
    keywords: ['backend engineer'],
    location: 'Hyderabad',
    max_pages: 5
  })
});
const { job_id } = await triggerResponse.json();

// 2. Poll until complete
let status = 'pending';
while (status !== 'completed' && status !== 'failed') {
  await new Promise(r => setTimeout(r, 3000));
  const poll = await fetch(`https://scraper.tayog.com/api/v1/scrape/${job_id}`);
  const data = await poll.json();
  status = data.status;
}

// 3. Fetch results
const jobsRes = await fetch('https://scraper.tayog.com/api/v1/jobs?location=Hyderabad&page=1');
const { data: jobs, total } = await jobsRes.json();
```

---

## Deployment

### Option A: AWS EC2 (recommended for production)

```bash
# 1. Launch Ubuntu 22.04 t3.medium (min 2 vCPU, 4GB RAM)
# 2. SSH in and install Docker
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin

# 3. Clone and configure
git clone https://github.com/yourorg/tayog-scraper.git
cd tayog-scraper && cp .env.example .env
# Edit .env with production secrets

# 4. Run
docker compose up -d --build

# 5. Set up Nginx reverse proxy + SSL (Certbot)
sudo apt install nginx certbot python3-certbot-nginx
sudo certbot --nginx -d scraper.tayog.com
```

### Option B: Render.com (quick deployment)

1. Connect GitHub repo to Render
2. Create a **Web Service** — set start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Create a **Background Worker** — set start command: `celery -A app.tasks.celery_app.celery_app worker --loglevel=info`
4. Add **Render Redis** and **MongoDB Atlas** (free tier) as environment variables
5. Deploy

### Option C: AWS ECS / Kubernetes (enterprise scale)

- Push Docker image to ECR
- Define ECS Task Definitions for api / worker / beat
- Use ALB for load balancing
- Use DocumentDB (MongoDB-compatible) + ElastiCache (Redis)
- Set up CloudWatch for logs and alarms

---

## Configuration

All configuration is via environment variables (see `.env.example`).  
Key variables:

| Variable               | Default       | Description                        |
|------------------------|---------------|------------------------------------|
| `MONGODB_URL`          | localhost      | MongoDB connection string          |
| `CELERY_BROKER_URL`    | redis://...    | Redis URL for Celery               |
| `SCRAPER_CONCURRENCY`  | 8             | Parallel requests per domain       |
| `SCRAPER_DELAY`        | 1.5           | Seconds between requests           |
| `MAX_RETRY_ATTEMPTS`   | 3             | Retry attempts on failure          |
| `LOG_FORMAT`           | json          | `json` (prod) or `text` (dev)      |
| `ENV`                  | development   | `development` / `production`       |

---

## Monitoring

- **Flower UI** — `http://localhost:5555` — real-time Celery task monitor
- **FastAPI docs** — `http://localhost:8000/docs` — Swagger UI
- **Health endpoint** — `GET /health` — liveness probe
- **Ready endpoint** — `GET /ready` — readiness probe (DB ping)
- **Prometheus** — metrics exposed at `/metrics` (via `prometheus-fastapi-instrumentator`)
- **Sentry** — set `SENTRY_DSN` in `.env` for error tracking

---

## Best Practices Implemented

| Practice                    | Implementation                                           |
|-----------------------------|----------------------------------------------------------|
| Non-root Docker user        | `USER tayog` in Dockerfile                               |
| Secrets via env vars        | `pydantic-settings`, never hardcoded                     |
| Async all the way           | `motor` (async MongoDB), `httpx` async, `asyncio`        |
| Idempotent upserts          | SHA-256 fingerprint dedup in MongoDB                     |
| Graceful shutdown           | FastAPI `lifespan` context manager                       |
| Rate limiting               | Per-domain `asyncio.Semaphore` + configurable delay      |
| Retry with backoff          | `tenacity` library on all HTTP calls                     |
| Structured logging          | JSON log lines → CloudWatch / ELK / Datadog              |
| Separation of concerns      | Repository pattern, engine façade, processor module      |
| Health / readiness probes   | `/health` and `/ready` for load balancer integration     |
| Horizontal scalability      | Stateless API + Celery workers scale independently       |
