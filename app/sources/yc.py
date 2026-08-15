"""YC (Y Combinator) job fetcher."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, accept_job

logger = logging.getLogger(__name__)


async def fetch_yc(
    max_results: int = 40,
    excludes: list[str] | None = None,
    max_company_pages: int = 60,
) -> list[RawJob]:
    """Best-effort YC jobs via public hiring company list + company job pages."""
    import html as html_lib
    from app.sources.utils import extract_json_array_after

    jobs: list[RawJob] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=45.0, headers=_BROWSER_HEADERS, follow_redirects=True) as client:
            try:
                resp = await client.get("https://yc-oss.github.io/api/companies/hiring.json")
                resp.raise_for_status()
                companies = resp.json()
            except Exception as exc:
                logger.warning("YC hiring list fetch failed: %s", exc)
                return []
            if not isinstance(companies, list):
                return []

            recent_pref = ("W26", "S25", "W25", "S24", "W24", "S23", "W23")

            def _rank(c: dict[str, Any]) -> tuple[int, str]:
                batch = str(c.get("batch") or "")
                try:
                    pref = recent_pref.index(batch)
                except ValueError:
                    pref = 99
                return (pref, str(c.get("name") or ""))

            ordered = sorted(
                [c for c in companies if isinstance(c, dict) and c.get("slug")],
                key=_rank,
            )[:max_company_pages]

            for company in ordered:
                if len(jobs) >= max_results:
                    break
                slug = str(company.get("slug") or "").strip()
                name = str(company.get("name") or slug)
                if not slug:
                    continue
                page_url = f"https://www.ycombinator.com/companies/{slug}/jobs"
                try:
                    page = await client.get(page_url)
                    if page.status_code >= 400:
                        continue
                    text = html_lib.unescape(page.text)
                    postings = extract_json_array_after(text, '"jobPostings":') or []
                except Exception as exc:
                    logger.debug("YC company page %s failed: %s", slug, exc)
                    continue
                one_liner = str(company.get("one_liner") or company.get("oneLiner") or "")
                for item in postings:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()
                    if not title:
                        continue
                    role = str(item.get("role") or item.get("prettyRole") or "").lower()
                    if role and role not in {"eng", "engineering", ""} and "engineer" not in title.lower():
                        if role in {"design", "sales", "marketing", "operations", "recruiting"}:
                            continue
                    ext = str(item.get("id") or "")
                    if not ext or ext in seen:
                        continue
                    seen.add(ext)
                    path = str(item.get("url") or "")
                    if path.startswith("/"):
                        job_url = f"https://www.ycombinator.com{path}"
                    else:
                        job_url = path or page_url
                    skills = item.get("skills") or []
                    skill_txt = ", ".join(str(s) for s in skills if s)
                    desc_parts = [
                        one_liner,
                        f"Role: {item.get('prettyRole') or item.get('roleSpecificType') or ''}".strip(),
                        f"Type: {item.get('type') or ''}".strip(),
                        f"Experience: {item.get('minExperience') or ''}".strip(),
                        f"Visa: {item.get('visa') or ''}".strip(),
                        f"Skills: {skill_txt}".strip(": "),
                    ]
                    desc_probe = "\n".join(p for p in desc_parts if p)
                    if not accept_job(title, desc_probe, excludes):
                        continue
                    salary = str(item.get("salaryRange") or "")
                    equity = str(item.get("equityRange") or "")
                    if equity:
                        salary = f"{salary} · Equity {equity}".strip(" ·")
                    jobs.append(
                        RawJob(
                            source="yc",
                            external_id=ext,
                            title=title,
                            company=str(item.get("companyName") or name),
                            location=str(item.get("location") or "N/A"),
                            url=job_url,
                            description="\n".join(p for p in desc_parts if p and not p.endswith(": ")).strip()[:8000],
                            salary=salary,
                        )
                    )
                    if len(jobs) >= max_results:
                        break
    except Exception as exc:
        logger.warning("YC fetch failed: %s", exc)
        return []
    return jobs
