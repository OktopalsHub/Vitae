"""ATS board fetchers: Greenhouse, Lever, Ashby."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx

from app.sources.base import RawJob
from app.sources.utils import (
    _BROWSER_HEADERS,
    accept_job,
    slug_id,
    strip_html,
    validate_board_name,
)

logger = logging.getLogger(__name__)


async def fetch_greenhouse(
    board: str, max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    try:
        board = validate_board_name(board)
    except ValueError:
        logger.warning("fetch_greenhouse: invalid board name %r — skipped", board)
        return []
    jobs: list[RawJob] = []
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params={"content": "true"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Greenhouse fetch failed for %s: %s", board, exc)
            return []
    for item in data.get("jobs") or []:
        title = item.get("title") or ""
        desc = strip_html(item.get("content") or "")
        if not accept_job(title, desc, excludes):
            continue
        loc = ""
        if item.get("location"):
            loc = item["location"].get("name") or ""
        jobs.append(
            RawJob(
                source="greenhouse",
                external_id=f"{board}:{item.get('id')}",
                title=title,
                company=board,
                location=loc,
                url=item.get("absolute_url") or "",
                description=desc[:8000],
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


async def fetch_lever(
    site: str, max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    try:
        site = validate_board_name(site)
    except ValueError:
        logger.warning("fetch_lever: invalid site name %r — skipped", site)
        return []
    jobs: list[RawJob] = []
    url = f"https://api.lever.co/v0/postings/{site}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params={"mode": "json"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Lever fetch failed for %s: %s", site, exc)
            return []
    if not isinstance(data, list):
        return []
    for item in data:
        title = item.get("text") or ""
        desc_parts = [
            item.get("descriptionPlain") or "",
            item.get("additionalPlain") or "",
        ]
        for block in item.get("lists") or []:
            desc_parts.append(strip_html(block.get("content") or ""))
            desc_parts.append(block.get("text") or "")
        desc = "\n".join(p for p in desc_parts if p)[:8000]
        if not accept_job(title, desc, excludes):
            continue
        cats = item.get("categories") or {}
        location = cats.get("location") or ""
        jobs.append(
            RawJob(
                source="lever",
                external_id=item.get("id") or slug_id("lever", site, title),
                title=title,
                company=site,
                location=location,
                url=item.get("hostedUrl") or item.get("applyUrl") or "",
                description=desc,
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


def _ashby_board_name(board: str) -> str:
    raw = (board or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        tail = parsed.path.rstrip("/").split("/")[-1]
        return unquote(tail)
    return unquote(raw)


def _ashby_company_name(board: str) -> str:
    name = _ashby_board_name(board)
    return name or "Ashby"


def _ashby_salary_summary(compensation: Any) -> str:
    if not isinstance(compensation, dict):
        return ""
    summary = str(compensation.get("compensationTierSummary") or "").strip()
    if summary:
        return summary
    return str(compensation.get("scrapeableCompensationSalarySummary") or "").strip()


async def fetch_ashby(
    board: str, max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    board_name = _ashby_board_name(board)
    if not board_name:
        return []
    try:
        validate_board_name(board_name, allow_spaces=True)
    except ValueError:
        logger.warning("fetch_ashby: invalid board name %r — skipped", board_name)
        return []
    jobs: list[RawJob] = []
    url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(board_name)}"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, params={"includeCompensation": "true"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Ashby fetch failed for %s: %s", board_name, exc)
            return []
    company = _ashby_company_name(board)
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue
        if item.get("isListed") is False:
            continue
        title = str(item.get("title") or "").strip()
        desc = str(item.get("descriptionPlain") or "").strip()
        if not desc:
            desc = strip_html(str(item.get("descriptionHtml") or ""))
        if not accept_job(title, desc, excludes):
            continue
        location = str(item.get("location") or "").strip()
        if not location:
            secondary = item.get("secondaryLocations") or []
            if isinstance(secondary, list):
                names = [str(loc or "").strip() for loc in secondary if str(loc or "").strip()]
                if names:
                    location = " / ".join(names)
        if not location and item.get("isRemote"):
            location = "Remote"
        ext = str(item.get("id") or "")
        if not ext:
            ext = slug_id("ashby", board_name, title, str(item.get("jobUrl") or ""))
        jobs.append(
            RawJob(
                source="ashby",
                external_id=ext,
                title=title or "Untitled",
                company=company,
                location=location,
                url=str(item.get("jobUrl") or item.get("applyUrl") or "").strip(),
                description=desc[:8000],
                salary=_ashby_salary_summary(item.get("compensation")),
                extra={
                    "apply_url": str(item.get("applyUrl") or "").strip(),
                    "department": item.get("department") or "",
                    "team": item.get("team") or "",
                    "employment_type": item.get("employmentType") or "",
                    "workplace_type": item.get("workplaceType") or "",
                    "published_at": item.get("publishedAt") or "",
                },
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs
