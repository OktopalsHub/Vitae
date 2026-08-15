"""Wellfound (AngelList) job fetcher."""

from __future__ import annotations

import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, accept_job

logger = logging.getLogger(__name__)


async def fetch_wellfound(
    urls: list[str] | None = None,
    max_results: int = 40,
    excludes: list[str] | None = None,
) -> list[RawJob]:
    """Best-effort Wellfound SEO landing pages via embedded Apollo/__NEXT_DATA__."""
    targets = list(urls or []) or [
        "https://wellfound.com/role/software-engineer",
        "https://wellfound.com/role/backend-engineer",
        "https://wellfound.com/role/l/software-engineer/remote",
    ]
    jobs: list[RawJob] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=40.0, headers=_BROWSER_HEADERS, follow_redirects=True) as client:
        for page_url in targets:
            if len(jobs) >= max_results:
                break
            try:
                resp = await client.get(page_url)
                if resp.status_code >= 400:
                    logger.warning("Wellfound blocked/empty (%s): HTTP %s", page_url, resp.status_code)
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                nd = soup.find("script", id="__NEXT_DATA__")
                if not nd or not nd.string:
                    for href in re.findall(r"/jobs/(\d+)-([a-z0-9-]+)", resp.text, flags=re.I):
                        jid, slug = href
                        if jid in seen:
                            continue
                        title = slug.replace("-", " ").title()
                        if not accept_job(title, "", excludes):
                            continue
                        seen.add(jid)
                        jobs.append(
                            RawJob(
                                source="wellfound",
                                external_id=jid,
                                title=title,
                                company="",
                                location="N/A",
                                url=f"https://wellfound.com/jobs/{jid}-{slug}",
                                description=f"Discovered via {page_url}",
                            )
                        )
                        if len(jobs) >= max_results:
                            break
                    continue
                payload = json.loads(nd.string)
                data = (
                    ((payload.get("props") or {}).get("pageProps") or {})
                    .get("apolloState", {})
                    .get("data")
                    or {}
                )
                if not isinstance(data, dict):
                    continue
                startups = {
                    str(v.get("id")): v
                    for v in data.values()
                    if isinstance(v, dict) and v.get("__typename") == "StartupResult"
                }
                job_to_company: dict[str, str] = {}
                for st in startups.values():
                    for ref in st.get("highlightedJobListings") or []:
                        if isinstance(ref, dict) and ref.get("__ref"):
                            jid = str(ref["__ref"]).split(":")[-1]
                            job_to_company[jid] = str(st.get("name") or "")
                for node in data.values():
                    if not isinstance(node, dict):
                        continue
                    if node.get("__typename") != "JobListingSearchResult":
                        continue
                    title = str(node.get("title") or "").strip()
                    desc = str(node.get("description") or "")
                    if not title or not accept_job(title, desc, excludes):
                        continue
                    jid = str(node.get("id") or "")
                    slug = str(node.get("slug") or "job")
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    locs = node.get("locationNames") or []
                    if node.get("remote"):
                        loc = "Remote"
                        if locs:
                            loc = f"Remote · {', '.join(str(x) for x in locs)}"
                    else:
                        loc = ", ".join(str(x) for x in locs) if locs else "N/A"
                    company = job_to_company.get(jid) or ""
                    jobs.append(
                        RawJob(
                            source="wellfound",
                            external_id=jid,
                            title=title,
                            company=company,
                            location=loc,
                            url=f"https://wellfound.com/jobs/{jid}-{slug}",
                            description=str(node.get("description") or "")[:8000],
                            salary=str(node.get("compensation") or ""),
                        )
                    )
                    if len(jobs) >= max_results:
                        break
            except Exception as exc:
                logger.warning("Wellfound fetch failed for %s: %s", page_url, exc)
                continue
    return jobs
