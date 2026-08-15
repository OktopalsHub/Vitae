"""Company-specific fetchers: YC, Wellfound, ZipRecruiter, BruntWork."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, accept_job, slug_id, strip_html

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
