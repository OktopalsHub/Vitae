from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.sources import fetchers
from app.sources import boards as fetchers_boards
from app.sources import company as fetchers_company
from app.sources.base import RawJob


class _FakeResponse:
    def __init__(self, *, text: str = "", json_data=None, status_code: int = 200):
        self.text = text
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._json_data is None:
            raise RuntimeError("No JSON payload")
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, responses: dict[tuple[str, tuple[tuple[str, str], ...]], _FakeResponse]):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params=None):
        key = (
            url,
            tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())),
        )
        if key not in self._responses:
            raise AssertionError(f"Unexpected request: {key}")
        return self._responses[key]


@pytest.mark.asyncio
async def test_fetch_ashby_uses_public_job_board_api(monkeypatch):
    payload = {
        "jobs": [
            {
                "id": "job-1",
                "title": "Senior Backend Engineer",
                "location": "Remote - Africa",
                "descriptionPlain": "Build backend APIs with Python and TypeScript.",
                "jobUrl": "https://jobs.ashbyhq.com/Scale%20Army%20Careers/job-1",
                "applyUrl": "https://jobs.ashbyhq.com/Scale%20Army%20Careers/job-1/application",
                "employmentType": "FullTime",
                "department": "Engineering",
                "team": "Platform",
                "workplaceType": "Remote",
                "isListed": True,
                "isRemote": True,
                "publishedAt": "2026-08-10T16:45:53.108+00:00",
                "compensation": {"compensationTierSummary": "$4k - $6k / month"},
            },
            {
                "id": "job-2",
                "title": "Hidden Backend Role",
                "location": "Remote",
                "descriptionPlain": "Hidden role",
                "jobUrl": "https://jobs.ashbyhq.com/Scale%20Army%20Careers/job-2",
                "isListed": False,
            },
        ]
    }
    responses = {
        (
                "https://api.ashbyhq.com/posting-api/job-board/Scale%20Army%20Careers",
                (("includeCompensation", "true"),),
            ): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_ashby("Scale Army Careers")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "ashby"
    assert job.external_id == "job-1"
    assert job.company == "Scale Army Careers"
    assert job.location == "Remote - Africa"
    assert job.salary == "$4k - $6k / month"
    assert job.extra["employment_type"] == "FullTime"
    assert job.extra["department"] == "Engineering"


@pytest.mark.asyncio
async def test_fetch_bruntwork_scrapes_search_and_detail_pages(monkeypatch):
    search_html = """
    <html><body>
      <a href="/jobs/12345">Backend Engineer Full Time (35 hours or more per week)</a>
      <a href="/jobs/12345">Backend Engineer Full Time (35 hours or more per week)</a>
      <a href="/jobs/99999/apply">Apply now</a>
    </body></html>
    """
    detail_html = """
    <html><body><main>
      <h1>Backend Engineer</h1>
      <p>Part-Time (20 hours per week)</p>
      <p>Build backend APIs with Python, FastAPI, and TypeScript.</p>
      <p>Job Category Engineering Job Type Full Time (35 hours or more per week) Work Schedule and Timezone Europe / UTC Published on Aug 12 2026 Apply Now</p>
      <p>BruntWork will never ask you for money or any other form of payment.</p>
    </main></body></html>
    """
    responses = {
        (
            "https://www.bruntworkcareers.co/search?priority=Normal",
            (),
        ): _FakeResponse(text=search_html),
        (
            "https://www.bruntworkcareers.co/jobs/12345",
            (),
        ): _FakeResponse(text=detail_html),
    }
    monkeypatch.setattr(fetchers_company.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_bruntwork()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "bruntwork"
    assert job.external_id == "12345"
    assert job.company == "BruntWork"
    assert job.location == "Europe / UTC"
    assert "FastAPI" in job.description
    assert job.extra["category"] == "Engineering"
    assert job.extra["job_type"] == "Full Time (35 hours or more per week)"


@pytest.mark.asyncio
async def test_iter_fetch_sources_includes_ashby_and_bruntwork(monkeypatch):
    empty = []

    async def _empty(*args, **kwargs):
        return empty

    async def _ashby(board, *args, **kwargs):
        return [RawJob(source="ashby", external_id=board, title="Backend Engineer")]

    async def _bruntwork(*args, **kwargs):
        return [RawJob(source="bruntwork", external_id="bw-1", title="Backend Engineer")]

    from app.sources import orchestrator as _orch
    monkeypatch.setattr(_orch, "fetch_adzuna", _empty)
    monkeypatch.setattr(_orch, "fetch_remoteok", _empty)
    monkeypatch.setattr(_orch, "fetch_remotive", _empty)
    monkeypatch.setattr(_orch, "fetch_arbeitnow", _empty)
    monkeypatch.setattr(_orch, "fetch_jobicy", _empty)
    monkeypatch.setattr(_orch, "fetch_jooble", _empty)
    monkeypatch.setattr(_orch, "fetch_greenhouse", _empty)
    monkeypatch.setattr(_orch, "fetch_lever", _empty)
    monkeypatch.setattr(_orch, "fetch_yc", _empty)
    monkeypatch.setattr(_orch, "fetch_wellfound", _empty)
    monkeypatch.setattr(_orch, "fetch_ziprecruiter", _empty)
    monkeypatch.setattr(_orch, "fetch_djinni", _empty)
    monkeypatch.setattr(_orch, "fetch_ashby", _ashby)
    monkeypatch.setattr(_orch, "fetch_bruntwork", _bruntwork)

    cfg = {
        "search": {"queries": ["backend"], "max_results_per_source": 5},
        "greenhouse_boards": [],
        "lever_boards": [],
        "ashby_boards": ["Scale Army Careers"],
        "sources": {
            "ashby": True,
            "bruntwork": True,
            "yc": False,
            "wellfound": False,
            "ziprecruiter": False,
            "djinni": False,
        },
    }
    settings = SimpleNamespace(adzuna_app_id="", adzuna_app_key="", jooble_api_key="")

    labels = []
    async for label, jobs in _orch.iter_fetch_sources(cfg, settings):
        if jobs:
            labels.append(label)

    assert "ashby:Scale Army Careers" in labels
    assert "bruntwork" in labels
