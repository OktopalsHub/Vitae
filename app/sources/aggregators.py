"""Aggregator / public job-API fetchers: Adzuna, RemoteOK, Remotive, Arbeitnow, Jobicy, Jooble."""

from __future__ import annotations

import logging
import re

import httpx

from app.sources.base import RawJob
from app.sources.utils import accept_job, slug_id, strip_html

logger = logging.getLogger(__name__)


async def fetch_adzuna(
    app_id: str,
    app_key: str,
    queries: list[str],
    country: str = "gb",
    results_per_page: int = 20,
    max_results: int = 40,
    excludes: list[str] | None = None,
) -> list[RawJob]:
    if not app_id or not app_key:
        return []
    jobs: list[RawJob] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        for query in queries:
            if len(jobs) >= max_results:
                break
            url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": results_per_page,
                "what": query,
                "content-type": "application/json",
            }
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Adzuna fetch failed for query %r: %s", query, exc)
                continue
            for item in data.get("results") or []:
                title = item.get("title") or "Untitled"
                desc = strip_html(item.get("description") or "")
                if not accept_job(title, desc, excludes):
                    continue
                ext = str(item.get("id") or "")
                if not ext or ext in seen:
                    continue
                seen.add(ext)
                company = (item.get("company") or {}).get("display_name") or ""
                location = (item.get("location") or {}).get("display_name") or ""
                salary = ""
                if item.get("salary_min") or item.get("salary_max"):
                    salary = f"{item.get('salary_min', '')}-{item.get('salary_max', '')} {item.get('salary_currency', '')}".strip()
                jobs.append(
                    RawJob(
                        source="adzuna",
                        external_id=ext,
                        title=title,
                        company=company,
                        location=location,
                        url=item.get("redirect_url") or item.get("adref") or "",
                        description=desc,
                        salary=salary,
                    )
                )
                if len(jobs) >= max_results:
                    break
    return jobs


async def fetch_remoteok(
    max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    headers = {"User-Agent": "vitae/1.0"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
        try:
            resp = await client.get("https://remoteok.com/api")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("RemoteOK fetch failed: %s", exc)
            return []
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            continue
        title = item.get("position") or "Untitled"
        tags = " ".join(item.get("tags") or [])
        desc = strip_html(item.get("description") or "")
        tag_blob = tags.lower()
        if not re.search(r"backend|node|typescript|nestjs|full.?stack", title, re.I):
            if not any(k in tag_blob for k in ("backend", "node", "typescript", "javascript", "api")):
                continue
        if not accept_job(title, f"{tags} {desc}", excludes):
            continue
        ext = str(item["id"])
        jobs.append(
            RawJob(
                source="remoteok",
                external_id=ext,
                title=title,
                company=item.get("company") or "",
                location=item.get("location") or "Remote",
                url=item.get("url") or f"https://remoteok.com/remote-jobs/{ext}",
                description=desc[:8000],
                salary=str(item.get("salary_max") or item.get("salary_min") or ""),
                extra={"tags": item.get("tags") or []},
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


async def fetch_remotive(
    max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                "https://remotive.com/api/remote-jobs",
                params={"category": "software-dev"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Remotive fetch failed: %s", exc)
            return []
    for item in data.get("jobs") or []:
        title = item.get("title") or ""
        desc = strip_html(item.get("description") or "")
        if not accept_job(title, desc, excludes):
            continue
        ext = str(item.get("id") or "")
        if not ext:
            continue
        jobs.append(
            RawJob(
                source="remotive",
                external_id=ext,
                title=title,
                company=item.get("company_name") or "",
                location=item.get("candidate_required_location") or "Remote",
                url=item.get("url") or "",
                description=desc[:8000],
                salary=str(item.get("salary") or ""),
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


async def fetch_arbeitnow(
    max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get("https://www.arbeitnow.com/api/job-board-api")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Arbeitnow fetch failed: %s", exc)
            return []
    for item in data.get("data") or []:
        title = item.get("title") or ""
        desc = strip_html(item.get("description") or "")
        if not accept_job(title, desc, excludes):
            continue
        loc_parts = item.get("location") or ""
        if item.get("remote"):
            loc_parts = f"Remote · {loc_parts}".strip(" ·")
        jobs.append(
            RawJob(
                source="arbeitnow",
                external_id=str(item.get("slug") or item.get("url") or slug_id("arbeitnow", title)),
                title=title,
                company=item.get("company_name") or "",
                location=loc_parts or ("Remote" if item.get("remote") else "N/A"),
                url=item.get("url") or "",
                description=desc[:8000],
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


async def fetch_jobicy(
    max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    params = {"count": min(max_results * 2, 50), "tag": "typescript"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get("https://jobicy.com/api/v2/remote-jobs", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Jobicy primary fetch failed (trying fallback): %s", exc)
            try:
                resp = await client.get(
                    "https://jobicy.com/api/v2/remote-jobs",
                    params={"count": min(max_results * 2, 50)},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc2:
                logger.warning("Jobicy fallback fetch failed: %s", exc2)
                return []
    for item in data.get("jobs") or []:
        title = item.get("jobTitle") or ""
        desc = strip_html(item.get("jobDescription") or "")
        if not accept_job(title, desc, excludes):
            continue
        jobs.append(
            RawJob(
                source="jobicy",
                external_id=str(item.get("id") or slug_id("jobicy", title, item.get("companyName") or "")),
                title=title,
                company=item.get("companyName") or "",
                location=item.get("jobGeo") or "Remote",
                url=item.get("url") or item.get("jobUrl") or "",
                description=desc[:8000],
                salary=str(item.get("annualSalaryMin") or item.get("salaryCurrency") or ""),
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


async def fetch_jooble(
    api_key: str,
    queries: list[str],
    max_results: int = 40,
    excludes: list[str] | None = None,
) -> list[RawJob]:
    if not api_key:
        return []
    jobs: list[RawJob] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=30.0) as client:
        for query in queries:
            if len(jobs) >= max_results:
                break
            url = f"https://jooble.org/api/{api_key}"
            payload = {"keywords": query, "location": "remote", "page": 1}
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Jooble fetch failed for query %r: %s", query, exc)
                continue
            for item in data.get("jobs") or []:
                title = item.get("title") or ""
                desc = strip_html(item.get("snippet") or item.get("description") or "")
                if not accept_job(title, desc, excludes):
                    continue
                ext = str(item.get("id") or item.get("link") or "")
                if not ext or ext in seen:
                    continue
                seen.add(ext)
                jobs.append(
                    RawJob(
                        source="jooble",
                        external_id=ext[:80],
                        title=title,
                        company=item.get("company") or "",
                        location=item.get("location") or "Remote",
                        url=item.get("link") or "",
                        description=desc[:8000],
                        salary=str(item.get("salary") or ""),
                    )
                )
                if len(jobs) >= max_results:
                    break
    return jobs
