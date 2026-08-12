"""RSS-based job fetchers: Djinni."""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, accept_job, slug_id, strip_html

logger = logging.getLogger(__name__)


async def fetch_djinni(
    max_results: int = 40,
    excludes: list[str] | None = None,
    feed_urls: list[str] | None = None,
) -> list[RawJob]:
    """Best-effort Djinni.co public RSS (Node.js / TypeScript focused feeds)."""
    urls = list(feed_urls or []) or [
        "https://djinni.co/jobs/rss/?primary_keyword=Node.js",
        "https://djinni.co/jobs/rss/?keywords=TypeScript+backend",
        "https://djinni.co/jobs/rss/?primary_keyword=JavaScript&keywords=TypeScript",
        "https://djinni.co/jobs/rss/?primary_keyword=Python",
        "https://djinni.co/jobs/rss/?keywords=FastAPI",
    ]
    jobs: list[RawJob] = []
    seen: set[str] = set()
    headers = dict(_BROWSER_HEADERS)
    headers["Accept"] = "application/rss+xml, application/xml, text/xml, */*"
    async with httpx.AsyncClient(timeout=35.0, headers=headers, follow_redirects=True) as client:
        for url in urls:
            if len(jobs) >= max_results:
                break
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    logger.warning("Djinni RSS HTTP %s for %s", resp.status_code, url)
                    continue
                soup = BeautifulSoup(resp.text, "lxml-xml")
                for item in soup.find_all("item"):
                    title = item.find("title").get_text(strip=True) if item.find("title") else ""
                    link = item.find("link").get_text(strip=True) if item.find("link") else ""
                    guid = item.find("guid").get_text(strip=True) if item.find("guid") else link or title
                    desc = strip_html(item.find("description").get_text() if item.find("description") else "")
                    if not title or not accept_job(title, desc, excludes):
                        continue
                    if guid in seen:
                        continue
                    seen.add(guid)
                    author = item.find("author") or item.find("dc:creator")
                    company = author.get_text(strip=True) if author else ""
                    jobs.append(
                        RawJob(
                            source="djinni",
                            external_id=slug_id("djinni", guid),
                            title=title,
                            company=company,
                            location="Remote / Europe / Ukraine",
                            url=link,
                            description=desc[:8000],
                        )
                    )
                    if len(jobs) >= max_results:
                        break
            except Exception as exc:
                logger.warning("Djinni RSS failed for %s: %s", url, exc)
                continue
    if not jobs:
        logger.warning("Djinni returned no jobs (feed empty or filtered out)")
    return jobs
