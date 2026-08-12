"""Backward-compatibility shim.

All fetcher logic has been split into focused sub-modules under app/sources/:
  - utils.py       — shared helpers (_slug_id, _strip_html, dedupe_raw_jobs, …)
  - boards.py      — fetch_greenhouse, fetch_lever, fetch_ashby
  - aggregators.py — fetch_adzuna, fetch_remoteok, fetch_remotive, fetch_arbeitnow,
                     fetch_jobicy, fetch_jooble
  - company.py     — fetch_yc, fetch_wellfound, fetch_ziprecruiter, fetch_bruntwork
  - rss.py         — fetch_djinni
  - paste.py       — ingest_pasted_job, _parse_jobposting_jsonld, _company_from_host
  - orchestrator.py — iter_fetch_sources, fetch_all_sources, _source_enabled

Import from those modules directly in new code.
This shim exists so existing call-sites continue to work without changes.
"""

from __future__ import annotations

# Re-export everything so `from app.sources.fetchers import X` still works.
from app.sources.aggregators import (
    fetch_adzuna,
    fetch_arbeitnow,
    fetch_jobicy,
    fetch_jooble,
    fetch_remoteok,
    fetch_remotive,
)
from app.sources.boards import (
    _ashby_board_name,
    _ashby_company_name,
    _ashby_salary_summary,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
)
from app.sources.company import (
    _bruntwork_job_links,
    _dedupe_text_lines,
    _parse_bruntwork_detail,
    fetch_bruntwork,
    fetch_wellfound,
    fetch_yc,
    fetch_ziprecruiter,
)
from app.sources.orchestrator import (
    _source_enabled,
    fetch_all_sources,
    iter_fetch_sources,
)
from app.sources.paste import (
    _company_from_host,
    _parse_jobposting_jsonld,
    ingest_pasted_job,
)
from app.sources.rss import fetch_djinni
from app.sources.utils import (
    _BROWSER_HEADERS,
    _BOARD_NAME_RE,
    _BOARD_SLUG_RE,
    accept_job as _accept_job,
    accept_title as _accept_title,
    dedupe_key as _dedupe_key,
    dedupe_raw_jobs,
    norm_key as _norm_key,
    slug_id as _slug_id,
    strip_html as _strip_html,
    title_company_key as _title_company_key,
    validate_board_name as _validate_board_name,
)

__all__ = [
    # utils
    "_BROWSER_HEADERS",
    "_slug_id",
    "_strip_html",
    "_accept_job",
    "_accept_title",
    "_norm_key",
    "_dedupe_key",
    "dedupe_raw_jobs",
    "_validate_board_name",
    # aggregators
    "fetch_adzuna",
    "fetch_remoteok",
    "fetch_remotive",
    "fetch_arbeitnow",
    "fetch_jobicy",
    "fetch_jooble",
    # boards
    "fetch_greenhouse",
    "fetch_lever",
    "fetch_ashby",
    "_ashby_board_name",
    "_ashby_company_name",
    "_ashby_salary_summary",
    # company
    "fetch_yc",
    "fetch_wellfound",
    "fetch_ziprecruiter",
    "fetch_bruntwork",
    "_bruntwork_job_links",
    "_dedupe_text_lines",
    "_parse_bruntwork_detail",
    # rss
    "fetch_djinni",
    # paste
    "ingest_pasted_job",
    "_parse_jobposting_jsonld",
    "_company_from_host",
    # orchestrator
    "iter_fetch_sources",
    "fetch_all_sources",
    "_source_enabled",
]
