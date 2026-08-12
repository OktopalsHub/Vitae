"""Paste/URL job ingestion."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.sources.base import RawJob
from app.sources.utils import _BROWSER_HEADERS, slug_id, strip_html

logger = logging.getLogger(__name__)


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
                out["description"] = strip_html(str(desc))[:12000]
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
        except Exception as exc:
            raise ValueError(f"Could not fetch URL safely: {exc}") from exc

    if not final_title:
        final_title = "Pasted Job"
    if not final_desc:
        final_desc = "(No description provided)"

    ext = slug_id("paste", final_url or final_title, final_company, final_desc[:200])
    return RawJob(
        source="paste",
        external_id=ext,
        title=final_title,
        company=final_company or "Unknown",
        location=final_location or "N/A",
        url=final_url,
        description=final_desc,
    )
