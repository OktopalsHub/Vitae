"""Firecrawl-backed open-web job discovery.

Firecrawl is an additional discovery layer, not a replacement for structured
job APIs or company ATS adapters. Search results are normalized into RawJob
records and then pass through the same catalogue/provenance pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.sources.base import RawJob
from app.sources.utils import accept_job, strip_html

log = logging.getLogger(__name__)

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"


def _external_id(url: str) -> str:
    return hashlib.sha256(url.split("?", 1)[0].rstrip("/").lower().encode()).hexdigest()[:40]


def _company_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    host = host.removeprefix("www.")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].replace("-", " ").title()
    return host.title()


def _clean_text(value: Any, limit: int = 8000) -> str:
    if not isinstance(value, str):
        return ""
    return strip_html(value).strip()[:limit]


def _extract_location(text: str) -> str:
    patterns = [
        r"(?i)(?:location|locations|based in|located in)\s*[:\-]\s*([^\n|]{2,100})",
        r"(?i)\b(remote(?:\s+(?:worldwide|anywhere|us|usa|uk|europe|africa))?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" .,:;-")[:120]
    return ""


async def fetch_firecrawl(
    api_key: str,
    queries: list[str],
    max_results: int = 40,
    excludes: list[str] | None = None,
    include_domains: list[str] | None = None,
) -> list[RawJob]:
    """Search the open web for job pages using Firecrawl Search."""
    if not api_key:
        return []

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    jobs: list[RawJob] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=45.0, headers=headers) as client:
        for query in queries:
            if len(jobs) >= max_results:
                break
            payload: dict[str, Any] = {
                "query": f"{query} jobs",
                "limit": min(max_results - len(jobs), 10),
                "sources": ["web"],
                "scrapeOptions": {"formats": ["markdown"]},
            }
            if include_domains:
                payload["includeDomains"] = include_domains
            try:
                response = await client.post(FIRECRAWL_SEARCH_URL, json=payload)
                response.raise_for_status()
                data = response.json()
            except Exception:
                log.exception("Firecrawl search failed for query %r", query)
                continue

            for item in data.get("data") or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                title = _clean_text(item.get("title") or "")
                description = _clean_text(item.get("description") or "")
                markdown = _clean_text(item.get("markdown") or "", 8000)
                if not url or not title:
                    continue

                blob = f"{title}\n{description}\n{markdown}"
                if not re.search(r"(?i)\b(job|engineer|developer|designer|manager|analyst|specialist|recruit|career)\b", blob):
                    continue
                if not accept_job(title, blob, excludes):
                    continue

                canonical = url.split("?", 1)[0].rstrip("/").lower()
                if canonical in seen:
                    continue
                seen.add(canonical)

                location = _extract_location(blob)
                remote_type = "remote" if re.search(r"(?i)\bremote\b", blob) else ""
                jobs.append(
                    RawJob(
                        source="firecrawl",
                        external_id=_external_id(url),
                        title=title[:500],
                        company=_company_from_url(url),
                        location=location or ("Remote" if remote_type else ""),
                        url=url,
                        description=(markdown or description)[:8000],
                        normalized_location=location,
                        remote_type=remote_type,
                        extra={
                            "discovery_query": query,
                            "discovery_provider": "firecrawl",
                        },
                    )
                )
                if len(jobs) >= max_results:
                    break

    return jobs
