import pytest

from app.sources.firecrawl import _company_from_url, _extract_location, fetch_firecrawl


def test_firecrawl_helpers_extract_basic_job_metadata():
    assert _company_from_url("https://jobs.acme.com/backend-engineer") == "Acme"
    assert _extract_location("Location: Lagos, Nigeria") == "Lagos, Nigeria"
    assert _extract_location("This is a fully remote role") == "remote"


@pytest.mark.asyncio
async def test_firecrawl_is_optional_without_api_key():
    assert await fetch_firecrawl("", ["backend engineer"], max_results=5) == []
