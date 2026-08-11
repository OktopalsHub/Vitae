from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from app.matching.filters import should_ingest_job
from app.sources.base import RawJob

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _slug_id(source: str, *parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(f"{source}:{raw}".encode()).hexdigest()[:20]


def _strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _accept_job(
    title: str,
    description: str = "",
    excludes: list[str] | None = None,
    *,
    require_typescript: bool = False,
) -> bool:
    return should_ingest_job(
        title,
        description,
        excludes,
        require_typescript=require_typescript,
    )


# Backward-compatible name used while call sites pass description explicitly.
def _accept_title(title: str, excludes: list[str] | None = None, description: str = "") -> bool:
    return _accept_job(title, description, excludes)


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _dedupe_key(job: RawJob) -> str:
    if job.url:
        url = job.url.split("?")[0].rstrip("/").lower()
        return f"url:{url}"
    return f"tc:{_norm_key(job.title)}|{_norm_key(job.company)}"


def _title_company_key(title: str, company: str) -> str:
    return f"tc:{_norm_key(title)}|{_norm_key(company)}"


def dedupe_raw_jobs(jobs: list[RawJob]) -> list[RawJob]:
    seen: set[str] = set()
    seen_tc: set[str] = set()
    out: list[RawJob] = []
    for job in jobs:
        key = _dedupe_key(job)
        tc = _title_company_key(job.title, job.company)
        if key in seen or tc in seen_tc:
            continue
        seen.add(key)
        seen_tc.add(tc)
        out.append(job)
    return out


async def fetch_adzuna(
    app_id: str,
    app_key: str,
    queries: list[str],
    country: str = "gb",
    results_per_page: int = 25,
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
            except Exception:
                continue
            for item in data.get("results") or []:
                title = item.get("title") or "Untitled"
                desc = _strip_html(item.get("description") or "")
                if not _accept_job(title, desc, excludes):
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
        except Exception:
            return []
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            continue
        title = item.get("position") or "Untitled"
        tags = " ".join(item.get("tags") or [])
        desc = _strip_html(item.get("description") or "")
        # Prefer backend-ish tags when title is generic software engineer
        tag_blob = tags.lower()
        if not re.search(r"backend|node|typescript|nestjs|full.?stack", title, re.I):
            if not any(k in tag_blob for k in ("backend", "node", "typescript", "javascript", "api")):
                continue
        if not _accept_job(title, f"{tags} {desc}", excludes):
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
        except Exception:
            return []
    for item in data.get("jobs") or []:
        title = item.get("title") or ""
        desc = _strip_html(item.get("description") or "")
        if not _accept_job(title, desc, excludes):
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
                salary=item.get("salary") or "",
            )
        )
        if len(jobs) >= max_results:
            break
    return jobs


async def fetch_greenhouse(
    board: str, max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params={"content": "true"})
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
    for item in data.get("jobs") or []:
        title = item.get("title") or ""
        desc = _strip_html(item.get("content") or "")
        if not _accept_job(title, desc, excludes):
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
    jobs: list[RawJob] = []
    url = f"https://api.lever.co/v0/postings/{site}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, params={"mode": "json"})
            resp.raise_for_status()
            data = resp.json()
        except Exception:
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
            desc_parts.append(_strip_html(block.get("content") or ""))
            desc_parts.append(block.get("text") or "")
        desc = "\n".join(p for p in desc_parts if p)[:8000]
        if not _accept_job(title, desc, excludes):
            continue
        cats = item.get("categories") or {}
        location = cats.get("location") or ""
        jobs.append(
            RawJob(
                source="lever",
                external_id=item.get("id") or _slug_id("lever", site, title),
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


def _extract_json_array_after(text: str, marker: str) -> list[Any] | None:
    idx = text.find(marker)
    if idx < 0:
        return None
    i = idx + len(marker)
    while i < len(text) and text[i] in " \n\r\t":
        i += 1
    if i >= len(text) or text[i] != "[":
        return None
    depth = 0
    start = i
    in_str = False
    esc = False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : j + 1])
                    return parsed if isinstance(parsed, list) else None
                except json.JSONDecodeError:
                    return None
    return None


def _parse_jobposting_jsonld(html: str) -> dict[str, str]:
    """Extract title/company/location/description from schema.org JobPosting blocks."""
    out: dict[str, str] = {}
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        # Expand @graph
        expanded: list[Any] = []
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                expanded.extend(node["@graph"])
            else:
                expanded.append(node)
        for node in expanded:
            if not isinstance(node, dict):
                continue
            ntype = node.get("@type")
            types = ntype if isinstance(ntype, list) else [ntype]
            if "JobPosting" not in types:
                continue
            if node.get("title") and not out.get("title"):
                out["title"] = str(node.get("title") or "").strip()
            desc = node.get("description")
            if desc and not out.get("description"):
                out["description"] = _strip_html(str(desc))[:12000]
            org = node.get("hiringOrganization") or {}
            if isinstance(org, dict) and org.get("name") and not out.get("company"):
                out["company"] = str(org.get("name") or "").strip()
            loc = node.get("jobLocation")
            if not out.get("location") and loc:
                locs = loc if isinstance(loc, list) else [loc]
                names: list[str] = []
                for item in locs:
                    if isinstance(item, dict):
                        addr = item.get("address") or {}
                        if isinstance(addr, dict):
                            parts = [
                                addr.get("addressLocality") or "",
                                addr.get("addressRegion") or "",
                                addr.get("addressCountry") or "",
                            ]
                            names.append(", ".join(p for p in parts if p))
                        elif item.get("name"):
                            names.append(str(item.get("name")))
                if names:
                    out["location"] = " / ".join(names)
            if node.get("jobLocationType") == "TELECOMMUTE" and not out.get("location"):
                out["location"] = "Remote"
    return out


def _company_from_host(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "").lower()
    known = {
        "wellfound.com": "Wellfound",
        "angel.co": "Wellfound",
        "workatastartup.com": "Y Combinator",
        "ycombinator.com": "Y Combinator",
        "ziprecruiter.com": "ZipRecruiter",
        "linkedin.com": "LinkedIn",
    }
    if host in known:
        return known[host]
    # company pages like wellfound.com/company/x handled elsewhere
    return host.split(".")[0].title() if host else "Unknown"


async def ingest_pasted_job(
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    url: str = "",
    description: str = "",
) -> RawJob:
    final_title = title.strip()
    final_company = company.strip()
    final_location = location.strip()
    final_desc = description.strip()
    final_url = url.strip()

    if final_url and not final_desc:
        from app.ssrf import fetch_public_url

        headers = dict(_BROWSER_HEADERS)
        try:
            html = await fetch_public_url(final_url, headers=headers, timeout=30.0)
            posted = _parse_jobposting_jsonld(html)
            if posted.get("title") and not final_title:
                final_title = posted["title"]
            if posted.get("company") and not final_company:
                final_company = posted["company"]
            if posted.get("location") and not final_location:
                final_location = posted["location"]
            if posted.get("description"):
                final_desc = posted["description"]

            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content") and not final_title:
                final_title = og_title["content"].strip()
            page_title = soup.title.string if soup.title and soup.title.string else ""
            if not final_title and page_title:
                final_title = page_title.strip()
            if not final_desc:
                main = soup.find("main") or soup.find("article") or soup.body
                final_desc = re.sub(
                    r"\s+", " ", (main.get_text(" ", strip=True) if main else "")
                )[:12000]
            if not final_company:
                final_company = _company_from_host(final_url)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Could not fetch URL safely: {exc}") from exc

    if not final_title:
        final_title = "Pasted Job"
    if not final_desc:
        final_desc = "(No description provided)"

    ext = _slug_id("paste", final_url or final_title, final_company, final_desc[:200])
    return RawJob(
        source="paste",
        external_id=ext,
        title=final_title,
        company=final_company or "Unknown",
        location=final_location or "N/A",
        url=final_url,
        description=final_desc,
    )


def _source_enabled(cfg: dict[str, Any], name: str, default: bool = True) -> bool:
    sources = cfg.get("sources")
    if not isinstance(sources, dict):
        return default
    if name not in sources:
        return default
    return bool(sources.get(name))


async def fetch_yc(
    max_results: int = 40,
    excludes: list[str] | None = None,
    max_company_pages: int = 60,
) -> list[RawJob]:
    """Best-effort YC jobs via public hiring company list + company job pages."""
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

            # Prefer recent batches, then the rest.
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
                    postings = _extract_json_array_after(text, '"jobPostings":') or []
                except Exception:
                    continue
                one_liner = str(company.get("one_liner") or company.get("oneLiner") or "")
                for item in postings:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()
                    if not title:
                        continue
                    # Prefer engineering-ish roles when role metadata exists
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
                    if not _accept_job(title, desc_probe, excludes):
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
                    # Fallback: link harvest
                    for href in re.findall(r"/jobs/(\d+)-([a-z0-9-]+)", resp.text, flags=re.I):
                        jid, slug = href
                        if jid in seen:
                            continue
                        title = slug.replace("-", " ").title()
                        if not _accept_job(title, "", excludes):
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
                    if not title or not _accept_job(title, desc, excludes):
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
                    company = job_to_company.get(jid) or "Wellfound startup"
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
                # RSS / Atom
                if "<rss" in text.lower() or "<feed" in text.lower() or "<item>" in text.lower():
                    soup = BeautifulSoup(text, "lxml-xml")
                    items = soup.find_all("item") or soup.find_all("entry")
                    for item in items:
                        title = (item.find("title").get_text(strip=True) if item.find("title") else "")
                        desc_el = item.find("description") or item.find("summary") or item.find("content")
                        desc = _strip_html(desc_el.get_text() if desc_el else "")
                        if not title or not _accept_job(title, desc, excludes):
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
                        company = company_el.get_text(strip=True) if company_el else "ZipRecruiter"
                        jobs.append(
                            RawJob(
                                source="ziprecruiter",
                                external_id=_slug_id("zip", guid),
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
                # HTML JobPosting
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
                            zdesc = _strip_html(str(node.get("description") or ""))
                            if not _accept_job(title, zdesc, excludes):
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
                                    external_id=_slug_id("zip", key),
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


async def fetch_arbeitnow(
    max_results: int = 40, excludes: list[str] | None = None
) -> list[RawJob]:
    jobs: list[RawJob] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get("https://www.arbeitnow.com/api/job-board-api")
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
    for item in data.get("data") or []:
        title = item.get("title") or ""
        desc = _strip_html(item.get("description") or "")
        if not _accept_job(title, desc, excludes):
            continue
        loc_parts = item.get("location") or ""
        if item.get("remote"):
            loc_parts = f"Remote · {loc_parts}".strip(" ·")
        jobs.append(
            RawJob(
                source="arbeitnow",
                external_id=str(item.get("slug") or item.get("url") or _slug_id("arbeitnow", title)),
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
    # Industry 1 often software; tag filters help but API may ignore unknown params
    params = {"count": min(max_results * 2, 50), "tag": "typescript"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get("https://jobicy.com/api/v2/remote-jobs", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            # Fallback without tag
            try:
                resp = await client.get(
                    "https://jobicy.com/api/v2/remote-jobs",
                    params={"count": min(max_results * 2, 50)},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                return []
    for item in data.get("jobs") or []:
        title = item.get("jobTitle") or ""
        desc = _strip_html(item.get("jobDescription") or "")
        if not _accept_job(title, desc, excludes):
            continue
        jobs.append(
            RawJob(
                source="jobicy",
                external_id=str(item.get("id") or _slug_id("jobicy", title, item.get("companyName") or "")),
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
            except Exception:
                continue
            for item in data.get("jobs") or []:
                title = item.get("title") or ""
                desc = _strip_html(item.get("snippet") or item.get("description") or "")
                if not _accept_job(title, desc, excludes):
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
                    desc = _strip_html(item.find("description").get_text() if item.find("description") else "")
                    if not title or not _accept_job(title, desc, excludes):
                        continue
                    if guid in seen:
                        continue
                    seen.add(guid)
                    # Company sometimes encoded in feed categories/author; fallback hostname.
                    author = item.find("author") or item.find("dc:creator")
                    company = author.get_text(strip=True) if author else ""
                    jobs.append(
                        RawJob(
                            source="djinni",
                            external_id=_slug_id("djinni", guid),
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


async def iter_fetch_sources(cfg: dict[str, Any], settings: Any):
    """Yield (source_label, jobs) per board so sync can commit incrementally."""
    search = cfg.get("search") or {}
    queries = list(search.get("queries") or ["software", "engineer", "developer", "remote"])
    max_per = int(search.get("max_results_per_source") or 100)
    adzuna_cfg = cfg.get("adzuna") or {}
    excludes: list[str] = []
    if search.get("apply_exclude_patterns"):
        excludes = list(cfg.get("exclude_title_patterns") or [])

    yield (
        "adzuna",
        await fetch_adzuna(
            settings.adzuna_app_id,
            settings.adzuna_app_key,
            queries,
            country=adzuna_cfg.get("country") or "gb",
            results_per_page=int(adzuna_cfg.get("results_per_page") or 50),
            max_results=max_per,
            excludes=excludes,
        ),
    )
    yield ("remoteok", await fetch_remoteok(max_per, excludes))
    yield ("remotive", await fetch_remotive(max_per, excludes))
    yield ("arbeitnow", await fetch_arbeitnow(max_per, excludes))
    yield ("jobicy", await fetch_jobicy(max_per, excludes))
    yield (
        "jooble",
        await fetch_jooble(
            getattr(settings, "jooble_api_key", "") or "",
            queries,
            max_per,
            excludes,
        ),
    )

    for board in cfg.get("greenhouse_boards") or []:
        yield (f"greenhouse:{board}", await fetch_greenhouse(board, max_per, excludes))
    for site in cfg.get("lever_boards") or []:
        yield (f"lever:{site}", await fetch_lever(site, max_per, excludes))

    if _source_enabled(cfg, "yc", True):
        yield ("yc", await fetch_yc(max_per, excludes))
    if _source_enabled(cfg, "wellfound", True):
        yield (
            "wellfound",
            await fetch_wellfound(
                list(cfg.get("wellfound_urls") or []),
                max_per,
                excludes,
            ),
        )
    if _source_enabled(cfg, "ziprecruiter", False):
        yield ("ziprecruiter", await fetch_ziprecruiter(queries, max_per, excludes))
    if _source_enabled(cfg, "djinni", True):
        yield (
            "djinni",
            await fetch_djinni(
                max_per,
                excludes,
                list(cfg.get("djinni_urls") or []) or None,
            ),
        )


async def fetch_all_sources(cfg: dict[str, Any], settings: Any) -> list[RawJob]:
    """Pull catalogue candidates from every enabled board — no role/stack gate."""
    results: list[RawJob] = []
    async for _label, batch in iter_fetch_sources(cfg, settings):
        results.extend(batch)
    return dedupe_raw_jobs(results)
