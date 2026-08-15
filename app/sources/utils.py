"""Shared utilities for all job-source fetchers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

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

_BOARD_NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]{1,120}$")
_BOARD_SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


def validate_board_name(name: str, *, allow_spaces: bool = False) -> str:
    """Validate a board/site name used in URL path construction.

    Raises ValueError if the name contains path separators, protocol characters,
    or other characters that could mutate the URL structure.
    """
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Board name must not be empty")
    pattern = _BOARD_NAME_RE if allow_spaces else _BOARD_SLUG_RE
    if not pattern.match(clean):
        raise ValueError(
            f"Board name {clean!r} contains invalid characters. "
            "Only alphanumeric, hyphens, underscores, and dots are allowed."
        )
    return clean


def slug_id(source: str, *parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(f"{source}:{raw}".encode()).hexdigest()[:20]


def strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def accept_job(
    title: str,
    description: str = "",
    excludes: list[str] | None = None,
    *,
    require_typescript: bool = False,
) -> bool:
    return should_ingest_job(title)


def accept_title(
    title: str,
    excludes: list[str] | None = None,
    description: str = "",
) -> bool:
    return accept_job(title)


def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def dedupe_key(job: RawJob) -> str:
    if job.url:
        url = job.url.split("?")[0].rstrip("/").lower()
        return f"url:{url}"
    return f"tc:{norm_key(job.title)}|{norm_key(job.company)}"


def title_company_key(title: str, company: str) -> str:
    return f"tc:{norm_key(title)}|{norm_key(company)}"


def dedupe_raw_jobs(jobs: list[RawJob]) -> list[RawJob]:
    seen: set[str] = set()
    seen_tc: set[str] = set()
    out: list[RawJob] = []
    for job in jobs:
        key = dedupe_key(job)
        tc = title_company_key(job.title, job.company)
        if key in seen or tc in seen_tc:
            continue
        seen.add(key)
        seen_tc.add(tc)
        out.append(job)
    return out


def extract_json_array_after(text: str, marker: str) -> list[Any] | None:
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
                except json.JSONDecodeError as exc:
                    logger.debug("JSON array parse failed: %s", exc)
                    return None
    return None
