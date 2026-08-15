"""ZipRecruiter job fetcher."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, accept_job, slug_id, strip_html

logger = logging.getLogger(__name__)


async def fetch_ziprecruiter(
    queries: list[str] | None = None,
    max_results: int = 40,
    excludes: list[str] | None = None,
) -> list[RawJob]:
    """Best-effort ZipRecruiter public RSS/search probes. Often blocked — soft-fails to []."""
    qlist = list(queries or ["Backend Engineer Remote"])[:4]
    jobs: list[RawJob] = []
    seen: set[str] = set()
    endpoints: list[str] = []
    for q in qlist:
        qq = quote_plus(q)
        endpoints.extend(
            [
                f"https://www.ziprecruiter.com/candidate/search/rss?search={qq}&location=Remote",
                f"https://www.ziprecruiter.com/jobs-search?search={qq}&location=Remote",
            ]
        )
    async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as client:
        for url in endpoints:
            if len(jobs) >= max_results:
                break
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    logger.warning("ZipRecruiter probe HTTP %s for %s", resp.status_code, url)
                    continue
                text = resp.text or ""
                if "Just a moment" in text or "cf-browser-verification" in text.lower():
                    logger.warning("ZipRecruiter bot challenge for %s", url)
                    continue
                if "<rss" in text.lower() or "<feed" in text.lower() or "<item>" in text.lower():
                    soup = BeautifulSoup(text, "lxml-xml")
                    items = soup.find_all("item") or soup.find_all("entry")
                    for item in items:
                        title = (item.find("title").get_text(strip=True) if item.find("title") else "")
                        desc_el = item.find("description") or item.find("summary") or item.find("content")
                        desc = strip_html(desc_el.get_text() if desc_el else "")
                        if not title or not accept_job(title, desc, excludes):
                            continue
                        link_el = item.find("link")
                        link = ""
                        if link_el:
                            link = link_el.get("href") or link_el.get_text(strip=True)
                        guid = item.find("guid").get_text(strip=True) if item.find("guid") else link or title
                        if guid in seen:
                            continue
                        seen.add(guid)
                        company_el = item.find("company") or item.find("author")
                        company = company_el.get_text(strip=True) if company_el else ""
                        jobs.append(
                            RawJob(
                                source="ziprecruiter",
                                external_id=slug_id("zip", guid),
                                title=title,
                                company=company,
                                location="Remote",
                                url=link,
                                description=desc[:8000],
                            )
                        )
                        if len(jobs) >= max_results:
                            break
                    continue
                soup = BeautifulSoup(text, "lxml")
                for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
                    raw = (script.string or "").strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    nodes = data if isinstance(data, list) else [data]
                    for node in nodes:
                        if isinstance(node, dict) and node.get("@type") == "JobPosting":
                            title = str(node.get("title") or "")
                            zdesc = strip_html(str(node.get("description") or ""))
                            if not accept_job(title, zdesc, excludes):
                                continue
                            org = node.get("hiringOrganization") or {}
                            company = org.get("name") if isinstance(org, dict) else ""
                            link = str(node.get("url") or "")
                            key = link or title
                            if key in seen:
                                continue
                            seen.add(key)
                            jobs.append(
                                RawJob(
                                    source="ziprecruiter",
                                    external_id=slug_id("zip", key),
                                    title=title,
                                    company=str(company or ""),
                                    location="Remote",
                                    url=link,
                                    description=zdesc[:8000],
                                )
                            )
                            if len(jobs) >= max_results:
                                break
            except Exception as exc:
                logger.warning("ZipRecruiter probe failed for %s: %s", url, exc)
                continue
    if not jobs:
        logger.warning("ZipRecruiter returned no jobs (blocked or no public feed); use Paste for listing URLs")
    return jobs
