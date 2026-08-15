"""BruntWork job fetcher."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, accept_job

logger = logging.getLogger(__name__)


def _bruntwork_job_links(html: str, limit: int) -> list[tuple[str, str, str]]:
    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        match = re.match(r"^/jobs/(\d+)(?:/.*)?$", href)
        if not match:
            continue
        job_id = match.group(1)
        url = urljoin("https://www.bruntworkcareers.co", f"/jobs/{job_id}")
        if url in seen:
            continue
        seen.add(url)
        title = " ".join(anchor.get_text(" ", strip=True).split())
        links.append((job_id, title, url))
        if len(links) >= limit:
            break
    return links


def _dedupe_text_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        line = " ".join((raw or "").split())
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _parse_bruntwork_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main") or soup.body or soup
    title_tag = main.find("h1") or soup.find("h1")
    title = " ".join(title_tag.get_text(" ", strip=True).split()) if title_tag else ""
    raw_lines = [tag.get_text(" ", strip=True) for tag in main.find_all(["h1", "h2", "h3", "p", "li", "span"])]
    lines = _dedupe_text_lines(raw_lines)
    drop_prefixes = (
        "login to candidate portal",
        "go back",
        "apply now",
        "share job",
        "privacy policy",
        "terms of use",
        "copyright",
        "bruntwork will never ask you for money",
    )
    filtered = [
        line
        for line in lines
        if not line.casefold().startswith(drop_prefixes)
    ]
    text_blob = "\n".join(filtered)
    location = ""
    location_match = re.search(
        r"Work Schedule and Timezone\s+(.*?)\s+Published on\b",
        text_blob,
        re.I | re.S,
    )
    if location_match:
        location = " ".join(location_match.group(1).split())
    category = ""
    cat_match = re.search(r"Category\s+(.*?)(?:\s+Job Type\b|\s+Published on\b|$)", text_blob, re.I | re.S)
    if cat_match:
        category = " ".join(cat_match.group(1).split())
    job_type = ""
    type_match = re.search(r"Job Type\s+(.*?)(?:\s+Work Schedule\b|\s+Published on\b|$)", text_blob, re.I | re.S)
    if type_match:
        job_type = " ".join(type_match.group(1).split())
    published = ""
    published_match = re.search(r"Published on\s+(.*?)(?:\s+Apply Now\b|$)", text_blob, re.I | re.S)
    if published_match:
        published = " ".join(published_match.group(1).split())
    description = "\n".join(filtered)
    if title:
        title_fold = title.casefold()
        description = "\n".join(
            line for line in filtered if line.casefold() != title_fold and line.casefold() != f"{title_fold} apply now share job"
        )
    return {
        "title": title,
        "location": location,
        "description": description[:8000],
        "category": category,
        "job_type": job_type,
        "published_on": published,
    }


async def fetch_bruntwork(
    max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    search_url = "https://www.bruntworkcareers.co/search?priority=Normal"
    async with httpx.AsyncClient(timeout=30.0, headers=_BROWSER_HEADERS, follow_redirects=True) as client:
        try:
            resp = await client.get(search_url)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("BruntWork search failed: %s", exc)
            return []
        for ext, link_title, url in _bruntwork_job_links(resp.text, max_results):
            try:
                detail = await client.get(url)
                detail.raise_for_status()
            except Exception as exc:
                logger.debug("BruntWork detail fetch failed for %s: %s", url, exc)
                continue
            parsed = _parse_bruntwork_detail(detail.text)
            title = parsed.get("title") or link_title or "Untitled"
            desc = parsed.get("description") or ""
            if not accept_job(title, desc, excludes):
                continue
            jobs.append(
                RawJob(
                    source="bruntwork",
                    external_id=ext,
                    title=title,
                    company="BruntWork",
                    location=parsed.get("location") or "Remote",
                    url=url,
                    description=desc,
                    extra={
                        "category": parsed.get("category") or "",
                        "job_type": parsed.get("job_type") or "",
                        "published_on": parsed.get("published_on") or "",
                    },
                )
            )
            if len(jobs) >= max_results:
                break
    return jobs
