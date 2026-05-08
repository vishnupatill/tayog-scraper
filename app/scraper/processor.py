"""
Job Data Processor — transforms raw scraped dicts into canonical JobListing dicts.
Pipeline: clean → normalize → extract skills → fingerprint → ready for DB upsert.
"""

import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Skill keyword taxonomy ─────────────────────────────────────────────────────

SKILLS_TAXONOMY = {
    # Languages
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#",
    "scala", "kotlin", "swift", "ruby", "php", "r",
    # Frameworks
    "fastapi", "django", "flask", "spring", "react", "angular", "vue", "nextjs",
    "express", "nestjs", "laravel", "rails",
    # Data
    "spark", "kafka", "airflow", "dbt", "pandas", "numpy", "sklearn", "tensorflow",
    "pytorch", "mlflow", "dask",
    # Databases
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "cassandra",
    "dynamodb", "bigquery", "snowflake", "databricks",
    # Cloud / DevOps
    "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "ansible",
    "jenkins", "github actions", "ci/cd",
    # Misc
    "rest api", "graphql", "grpc", "microservices", "rabbitmq", "celery",
    "scrapy", "playwright", "selenium", "beautifulsoup",
}

# ── Salary extraction ──────────────────────────────────────────────────────────

_SALARY_PATTERNS = [
    # ₹5 LPA – ₹12 LPA
    r"₹\s*(\d+\.?\d*)\s*(?:LPA|lpa|L)\s*[-–]\s*₹?\s*(\d+\.?\d*)\s*(?:LPA|lpa|L)",
    # $80,000 - $120,000
    r"\$\s*([\d,]+)\s*[-–]\s*\$?\s*([\d,]+)",
    # 5,00,000 - 10,00,000
    r"(\d[\d,]+)\s*[-–]\s*(\d[\d,]+)",
]


def _extract_salary(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    raw = raw.strip()
    for pattern in _SALARY_PATTERNS:
        m = re.search(pattern, raw)
        if m:
            try:
                lo = float(m.group(1).replace(",", ""))
                hi = float(m.group(2).replace(",", ""))
                # Distinguish LPA (< 500) from absolute
                if lo < 500:
                    lo *= 100_000
                    hi *= 100_000
                return {"min": lo, "max": hi, "currency": "INR", "period": "yearly", "raw": raw}
            except ValueError:
                pass
    return {"min": None, "max": None, "currency": "INR", "period": "yearly", "raw": raw}


# ── Skill extraction ───────────────────────────────────────────────────────────

def _extract_skills(text: str, seed_skills: List[str] = None) -> List[str]:
    combined = (text or "").lower()
    found = set(seed_skills or [])
    for skill in SKILLS_TAXONOMY:
        if re.search(r"\b" + re.escape(skill) + r"\b", combined):
            found.add(skill)
    return sorted(found)


# ── Text normalisation ─────────────────────────────────────────────────────────

def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove HTML tags that slipped through
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _normalise_location(loc: str) -> str:
    loc = _clean_text(loc)
    # Strip country codes like "India", "IN"
    loc = re.sub(r",?\s*(India|IN)\s*$", "", loc, flags=re.IGNORECASE).strip()
    return loc


def _normalise_job_type(title: str, description: str) -> str:
    combined = f"{title} {description}".lower()
    if "intern" in combined:
        return "internship"
    if any(w in combined for w in ("remote", "work from home", "wfh")):
        return "remote"
    if "contract" in combined or "freelance" in combined:
        return "contract"
    if "part time" in combined or "part-time" in combined:
        return "part_time"
    return "full_time"


def _parse_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).isoformat()
        except (ValueError, AttributeError):
            continue
    return None


def _fingerprint(title: str, company: str, location: str) -> str:
    """Stable SHA-256 hash for deduplication."""
    key = f"{title.lower().strip()}|{company.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()


# ── Processor class ────────────────────────────────────────────────────────────

class JobDataProcessor:
    def process(self, raw: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        """Transform a single raw dict → canonical job dict. Returns None if invalid."""
        title = _clean_text(raw.get("title", ""))
        company = _clean_text(raw.get("company", ""))
        location = _normalise_location(raw.get("location", ""))

        # Drop records missing critical fields
        if not title or not company:
            logger.debug("Dropped record missing title/company: %s", raw)
            return None

        description = _clean_text(raw.get("description", ""))
        seed_skills = [s.strip().lower() for s in (raw.get("skills") or []) if s.strip()]

        return {
            "title": title,
            "company": company,
            "location": location,
            "job_type": _normalise_job_type(title, description),
            "salary": _extract_salary(raw.get("salary_raw")),
            "skills": _extract_skills(description, seed_skills),
            "description": description[:5000],   # cap at 5k chars
            "url": raw.get("url", ""),
            "source": source,
            "posted_at": _parse_date(raw.get("posted_at_raw")),
            "scraped_at": datetime.utcnow().isoformat(),
            "fingerprint": _fingerprint(title, company, location),
            "is_active": True,
        }

    def process_batch(self, raw_items: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
        """Process a batch; skip invalid records and log stats."""
        results = []
        skipped = 0
        seen_fps = set()

        for item in raw_items:
            processed = self.process(item, source)
            if not processed:
                skipped += 1
                continue
            fp = processed["fingerprint"]
            if fp in seen_fps:
                skipped += 1
                continue
            seen_fps.add(fp)
            results.append(processed)

        logger.info(
            "Processed batch: total=%d valid=%d skipped=%d source=%s",
            len(raw_items), len(results), skipped, source,
        )
        return results
