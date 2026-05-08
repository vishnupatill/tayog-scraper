"""
Scraper Engine — orchestrates all scraping strategies.
Routes each source to the right spider (static HTTP vs Playwright JS rendering).
Implements: rate limiting, user-agent rotation, retry with exponential backoff.
"""

import asyncio
import logging
import random
from typing import Any, Dict, List

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── User-Agent pool ────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ── Per-domain rate limiter ────────────────────────────────────────────────────

_domain_semaphores: Dict[str, asyncio.Semaphore] = {}


def _get_semaphore(domain: str) -> asyncio.Semaphore:
    if domain not in _domain_semaphores:
        _domain_semaphores[domain] = asyncio.Semaphore(settings.SCRAPER_CONCURRENCY)
    return _domain_semaphores[domain]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _build_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    }


@retry(
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    stop=stop_after_attempt(settings.MAX_RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=settings.RETRY_BACKOFF, min=2, max=60),
    reraise=True,
)
async def fetch_page(client: httpx.AsyncClient, url: str, domain: str) -> str:
    """Fetch a single URL with rate limiting, UA rotation, and retries."""
    sem = _get_semaphore(domain)
    async with sem:
        await asyncio.sleep(settings.SCRAPER_DELAY + random.uniform(0, 1))
        response = await client.get(url, headers=_build_headers(), timeout=settings.SCRAPER_TIMEOUT)
        response.raise_for_status()
        return response.text


# ── Source-specific scrapers ───────────────────────────────────────────────────

class LinkedInScraper:
    """
    LinkedIn Jobs scraper.
    NOTE: In production, use the official LinkedIn API or a compliant provider.
    This demonstrates the pattern for structured static HTML scraping.
    """
    BASE_URL = "https://www.linkedin.com/jobs/search/"
    DOMAIN = "linkedin.com"

    async def scrape(self, keyword: str, location: str, max_pages: int) -> List[Dict[str, Any]]:
        jobs = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for page in range(max_pages):
                url = (
                    f"{self.BASE_URL}?keywords={keyword}&location={location}"
                    f"&start={page * 25}&f_TPR=r86400"
                )
                try:
                    html = await fetch_page(client, url, self.DOMAIN)
                    jobs.extend(self._parse(html))
                    logger.info("LinkedIn page %d/%d — %d jobs so far", page + 1, max_pages, len(jobs))
                except Exception as exc:
                    logger.warning("LinkedIn page %d failed: %s", page, exc)
                    break
        return jobs

    def _parse(self, html: str) -> List[Dict[str, Any]]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for card in soup.select("div.base-card"):
            try:
                jobs.append({
                    "title": card.select_one("h3.base-search-card__title")
                               .get_text(strip=True) if card.select_one("h3.base-search-card__title") else "",
                    "company": card.select_one("h4.base-search-card__subtitle")
                                  .get_text(strip=True) if card.select_one("h4.base-search-card__subtitle") else "",
                    "location": card.select_one("span.job-search-card__location")
                                    .get_text(strip=True) if card.select_one("span.job-search-card__location") else "",
                    "url": card.select_one("a.base-card__full-link")["href"]
                               if card.select_one("a.base-card__full-link") else "",
                    "posted_at_raw": card.select_one("time")["datetime"]
                                        if card.select_one("time") else None,
                })
            except Exception:
                continue
        return jobs


class NaukriScraper:
    """Naukri.com scraper — uses JSON API endpoints where available."""
    DOMAIN = "naukri.com"
    API_URL = "https://www.naukri.com/jobapi/v3/search"

    async def scrape(self, keyword: str, location: str, max_pages: int) -> List[Dict[str, Any]]:
        jobs = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for page in range(1, max_pages + 1):
                params = {
                    "noOfResults": 20,
                    "urlType": "search_by_key_loc",
                    "searchType": "adv",
                    "keyword": keyword,
                    "location": location,
                    "pageNo": page,
                    "k": keyword,
                    "l": location,
                    "seoKey": f"{keyword.replace(' ', '-')}-jobs-in-{location.replace(' ', '-')}",
                    "src": "jobsearchDesk",
                }
                try:
                    headers = _build_headers()
                    headers.update({
                        "appid": "109",
                        "systemid": "Naukri",
                        "Accept": "application/json",
                    })
                    resp = await client.get(
                        self.API_URL, params=params, headers=headers,
                        timeout=settings.SCRAPER_TIMEOUT,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    raw_jobs = data.get("jobDetails", [])
                    jobs.extend(self._parse(raw_jobs))
                    logger.info("Naukri page %d/%d — %d jobs", page, max_pages, len(jobs))
                except Exception as exc:
                    logger.warning("Naukri page %d failed: %s", page, exc)
                    break
        return jobs

    def _parse(self, raw_jobs: List[Dict]) -> List[Dict[str, Any]]:
        result = []
        for j in raw_jobs:
            result.append({
                "title": j.get("title", ""),
                "company": j.get("companyName", ""),
                "location": ", ".join(j.get("placeholders", [{}])[0].get("label", "").split(",")[:2]),
                "salary_raw": j.get("salary", ""),
                "skills": [s.get("label", "") for s in j.get("tagsAndSkills", [])],
                "url": j.get("jdURL", ""),
                "description": j.get("jobDescription", ""),
                "posted_at_raw": j.get("createdDate", ""),
            })
        return result


class PlaywrightScraper:
    """
    Playwright-based scraper for JavaScript-heavy sites.
    Handles infinite scroll, AJAX-loaded content, and dynamic pagination.
    """
    DOMAIN = "playwright"

    async def scrape_with_js(self, url: str, scroll_count: int = 5) -> str:
        """Return full page HTML after JS execution and infinite scroll."""
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=settings.PLAYWRIGHT_HEADLESS)
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = await context.new_page()

            # Block images/fonts to speed up scraping
            await page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
                lambda route: route.abort()
            )

            await page.goto(url, wait_until="domcontentloaded", timeout=settings.PLAYWRIGHT_TIMEOUT)
            await page.wait_for_timeout(2000)

            for _ in range(scroll_count):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)

            html = await page.content()
            await browser.close()
            return html


# ── Engine façade ──────────────────────────────────────────────────────────────

class ScraperEngine:
    """
    Unified interface — caller says 'scrape linkedin for X in Y'
    and engine picks the right spider and returns normalised raw dicts.
    """

    _spiders = {
        "linkedin": LinkedInScraper,
        "naukri": NaukriScraper,
    }

    async def scrape(
        self,
        source: str,
        keyword: str,
        location: str,
        max_pages: int = 5,
    ) -> List[Dict[str, Any]]:
        spider_cls = self._spiders.get(source.lower())
        if not spider_cls:
            logger.warning("No spider registered for source=%s", source)
            return []

        spider = spider_cls()
        logger.info("Engine dispatching %s spider", source)
        return await spider.scrape(keyword, location, max_pages)
