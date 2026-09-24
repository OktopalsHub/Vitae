"""Source orchestration: iter_fetch_sources, fetch_all_sources."""

from __future__ import annotations

from typing import Any

from app.sources.aggregators import (
    fetch_adzuna,
    fetch_arbeitnow,
    fetch_jobicy,
    fetch_jooble,
    fetch_remoteok,
    fetch_remotive,
)
from app.sources.base import RawJob
from app.sources.boards import fetch_ashby, fetch_greenhouse, fetch_lever
from app.sources.company import fetch_bruntwork, fetch_wellfound, fetch_yc, fetch_ziprecruiter
from app.sources.rss import fetch_djinni
from app.sources.utils import dedupe_raw_jobs


def _source_enabled(cfg: dict[str, Any], name: str, default: bool = True) -> bool:
    sources = cfg.get("sources")
    if not isinstance(sources, dict):
        return default
    if name not in sources:
        return default
    return bool(sources.get(name))


async def iter_fetch_sources(cfg: dict[str, Any], settings: Any):
    """Backward-compatible source iterator; failed sources yield an empty batch."""
    async for _label, ok, jobs, _error in iter_fetch_source_results(cfg, settings):
        yield _label, jobs if ok else []



async def iter_fetch_source_results(cfg: dict[str, Any], settings: Any):
    """Yield source health + jobs without turning one failed source into a failed sync."""
    async for label, fetcher in _source_jobs(cfg, settings):
        try:
            jobs = await fetcher()
            yield label, True, jobs, ""
        except Exception as exc:  # noqa: BLE001 - one source must not stop the catalogue
            yield label, False, [], str(exc)


async def _source_jobs(cfg: dict[str, Any], settings: Any):
    """Build lazy source fetch callables so each source has an isolated failure boundary."""
    search = cfg.get("search") or {}
    queries = list(search.get("queries") or ["software", "engineer", "developer", "remote"])
    max_per = int(search.get("max_results_per_source") or 100)
    adzuna_cfg = cfg.get("adzuna") or {}
    excludes: list[str] = []
    if search.get("apply_exclude_patterns"):
        excludes = list(cfg.get("exclude_title_patterns") or [])

    yield "adzuna", lambda: fetch_adzuna(
        settings.adzuna_app_id, settings.adzuna_app_key, queries,
        country=adzuna_cfg.get("country") or "gb",
        results_per_page=int(adzuna_cfg.get("results_per_page") or 50),
        max_results=max_per, excludes=excludes,
    )
    yield "remoteok", lambda: fetch_remoteok(max_per, excludes)
    yield "remotive", lambda: fetch_remotive(max_per, excludes)
    yield "arbeitnow", lambda: fetch_arbeitnow(max_per, excludes)
    yield "jobicy", lambda: fetch_jobicy(max_per, excludes)
    yield "jooble", lambda: fetch_jooble(
        getattr(settings, "jooble_api_key", "") or "", queries, max_per, excludes
    )
    for board in cfg.get("greenhouse_boards") or []:
        yield f"greenhouse:{board}", lambda board=board: fetch_greenhouse(board, max_per, excludes)
    for site in cfg.get("lever_boards") or []:
        yield f"lever:{site}", lambda site=site: fetch_lever(site, max_per, excludes)
    if _source_enabled(cfg, "ashby", True):
        for board in cfg.get("ashby_boards") or []:
            yield f"ashby:{board}", lambda board=board: fetch_ashby(board, max_per, excludes)
    if _source_enabled(cfg, "bruntwork", True):
        yield "bruntwork", lambda: fetch_bruntwork(max_per, excludes)
    if _source_enabled(cfg, "yc", True):
        yield "yc", lambda: fetch_yc(max_per, excludes)
    if _source_enabled(cfg, "wellfound", True):
        yield "wellfound", lambda: fetch_wellfound(
            list(cfg.get("wellfound_urls") or []), max_per, excludes
        )
    if _source_enabled(cfg, "ziprecruiter", False):
        yield "ziprecruiter", lambda: fetch_ziprecruiter(queries, max_per, excludes)
    if _source_enabled(cfg, "djinni", True):
        yield "djinni", lambda: fetch_djinni(
            max_per, excludes, list(cfg.get("djinni_urls") or []) or None
        )


async def fetch_all_sources(cfg: dict[str, Any], settings: Any) -> list[RawJob]:
    """Pull catalogue candidates from every enabled board — failed sources are isolated."""
    results: list[RawJob] = []
    async for _label, ok, batch, _error in iter_fetch_source_results(cfg, settings):
        if ok:
            results.extend(batch)
    return dedupe_raw_jobs(results)
