from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.sources import fetchers
from app.sources import boards as fetchers_boards
from app.sources import bruntwork as fetchers_bruntwork
from app.sources import company as fetchers_company
from app.sources import aggregators as fetchers_agg
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
    def __init__(self, responses: dict, post_responses: dict | None = None):
        self._responses = responses
        self._post_responses = post_responses or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params=None):
        key = (
            url,
            tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())),
        )
        if key in self._responses:
            return self._responses[key]
        if url in self._responses:
            return self._responses[url]
        raise AssertionError(f"Unexpected GET request: {key}")

    async def post(self, url: str, json=None, params=None):
        key = (
            url,
            tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())),
        )
        if key in self._post_responses:
            return self._post_responses[key]
        if url in self._post_responses:
            return self._post_responses[url]
        raise AssertionError(f"Unexpected POST request: {key}")


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_greenhouse_basic(monkeypatch):
    payload = {
        "jobs": [
            {
                "id": 1001,
                "title": "Backend Engineer",
                "content": "Build APIs with Python and FastAPI.",
                "location": {"name": "Remote"},
                "absolute_url": "https://boards.greenhouse.io/testco/jobs/1001",
            }
        ]
    }
    responses = {
        ("https://boards-api.greenhouse.io/v1/boards/testco/jobs", (("content", "true"),)): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_greenhouse("testco")

    assert len(jobs) == 1
    assert jobs[0].source == "greenhouse"
    assert jobs[0].external_id == "testco:1001"
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].company == "testco"
    assert jobs[0].location == "Remote"


@pytest.mark.asyncio
async def test_fetch_greenhouse_returns_multiple(monkeypatch):
    payload = {
        "jobs": [
            {"id": 1, "title": "Sales Rep", "content": "Sell stuff", "location": {"name": "NY"}},
            {"id": 2, "title": "Backend Engineer", "content": "Build APIs", "location": {"name": "Remote"}},
        ]
    }
    responses = {
        ("https://boards-api.greenhouse.io/v1/boards/testco/jobs", (("content", "true"),)): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_greenhouse("testco")

    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_fetch_greenhouse_invalid_board():
    jobs = await fetchers.fetch_greenhouse("../../../etc/passwd")
    assert jobs == []


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_lever_basic(monkeypatch):
    payload = [
        {
            "id": "lev-1",
            "text": "Senior Backend Engineer",
            "descriptionPlain": "Design and build scalable systems.",
            "additionalPlain": "Remote-friendly role.",
            "categories": {"location": "Remote"},
            "hostedUrl": "https://jobs.lever.co/testco/lev-1",
        }
    ]
    responses = {
        ("https://api.lever.co/v0/postings/testco", (("mode", "json"),)): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_lever("testco")

    assert len(jobs) == 1
    assert jobs[0].source == "lever"
    assert jobs[0].title == "Senior Backend Engineer"
    assert "Remote" in jobs[0].location


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

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
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

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


# ---------------------------------------------------------------------------
# BruntWork
# ---------------------------------------------------------------------------

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
        ("https://www.bruntworkcareers.co/search?priority=Normal", ()): _FakeResponse(text=search_html),
        ("https://www.bruntworkcareers.co/jobs/12345", ()): _FakeResponse(text=detail_html),
    }
    monkeypatch.setattr(fetchers_bruntwork.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

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


# ---------------------------------------------------------------------------
# RemoteOK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_remoteok_filters_by_title(monkeypatch):
    payload = [
        {"id": "r1", "position": "Backend Engineer", "company": "Acme", "location": "Remote", "tags": ["python", "api"]},
        {"id": "r2", "position": "Marketing Manager", "company": "Biz", "location": "NYC", "tags": ["marketing"]},
    ]
    responses = {
        ("https://remoteok.com/api", ()): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_agg.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_remoteok()

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Engineer"


# ---------------------------------------------------------------------------
# Remotive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_remotive_basic(monkeypatch):
    payload = {
        "jobs": [
            {"id": "rem-1", "title": "Full Stack Engineer", "company_name": "StartupCo", "candidate_required_location": "Remote", "description": "Build web apps"},
        ]
    }
    responses = {
        ("https://remotive.com/api/remote-jobs", (("category", "software-dev"),)): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_agg.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_remotive()

    assert len(jobs) == 1
    assert jobs[0].source == "remotive"
    assert jobs[0].company == "StartupCo"


# ---------------------------------------------------------------------------
# Arbeitnow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_arbeitnow_basic(monkeypatch):
    payload = {
        "data": [
            {"title": "Python Developer", "company_name": "CodeCo", "location": "Berlin", "url": "https://arbeitnow.com/job/1", "slug": "job-1"},
        ]
    }
    responses = {
        ("https://www.arbeitnow.com/api/job-board-api", ()): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_agg.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_arbeitnow()

    assert len(jobs) == 1
    assert jobs[0].source == "arbeitnow"
    assert jobs[0].company == "CodeCo"


# ---------------------------------------------------------------------------
# Jooble
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_jooble_basic(monkeypatch):
    payload = {"jobs": [{"title": "Node.js Developer", "company": "TechCo", "location": "Remote", "id": "j1", "link": "https://jooble.org/j1"}]}
    post_responses = {
        ("https://jooble.org/api/test-key", ()): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_agg.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient({}, post_responses))

    jobs = await fetchers.fetch_jooble("test-key", ["nodejs"])

    assert len(jobs) == 1
    assert jobs[0].source == "jooble"


@pytest.mark.asyncio
async def test_fetch_jooble_empty_key():
    jobs = await fetchers.fetch_jooble("", ["nodejs"])
    assert jobs == []


# ---------------------------------------------------------------------------
# Error handling — fetchers return [] on HTTP errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_greenhouse_returns_empty_on_error(monkeypatch):
    responses = {
        ("https://boards-api.greenhouse.io/v1/boards/testco/jobs", (("content", "true"),)): _FakeResponse(status_code=500),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_greenhouse("testco")
    assert jobs == []


@pytest.mark.asyncio
async def test_fetch_lever_returns_empty_on_error(monkeypatch):
    responses = {
        ("https://api.lever.co/v0/postings/testco", (("mode", "json"),)): _FakeResponse(status_code=500),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_lever("testco")
    assert jobs == []


@pytest.mark.asyncio
async def test_fetch_ashby_returns_empty_on_error(monkeypatch):
    responses = {
        ("https://api.ashbyhq.com/posting-api/job-board/test", (("includeCompensation", "true"),)): _FakeResponse(status_code=500),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_ashby("test")
    assert jobs == []


# ---------------------------------------------------------------------------
# Max results limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_greenhouse_respects_max_results(monkeypatch):
    payload = {
        "jobs": [
            {"id": i, "title": f"Engineer {i}", "content": "Build stuff", "location": {"name": "Remote"}}
            for i in range(10)
        ]
    }
    responses = {
        ("https://boards-api.greenhouse.io/v1/boards/testco/jobs", (("content", "true"),)): _FakeResponse(json_data=payload),
    }
    monkeypatch.setattr(fetchers_boards.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))

    jobs = await fetchers.fetch_greenhouse("testco", max_results=3)
    assert len(jobs) == 3


# ---------------------------------------------------------------------------
# iter_fetch_sources integration
# ---------------------------------------------------------------------------

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
