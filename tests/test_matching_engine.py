import json

from app.matching.scorer import MATCHING_ALGORITHM_VERSION, score_job_versioned
from app.matching.score_cache import score_fingerprint, score_listing_cached


def test_matching_result_has_stable_version_and_explanation():
    profile = {"skills": ["Python", "FastAPI", "PostgreSQL"]}
    cfg = {
        "title_keywords": ["backend", "software engineer"],
        "penalty_keywords": ["java"],
        "search": {"prefer_remote": True},
    }
    job = {
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Python FastAPI PostgreSQL APIs",
        "url": "https://example.com/jobs/1",
        "salary": "",
    }

    result = score_job_versioned(job, profile, cfg)

    assert result["algorithm_version"] == MATCHING_ALGORITHM_VERSION
    assert 0 <= result["score"] <= 100
    assert isinstance(result["positives"], list)
    assert isinstance(result["penalties"], list)
    assert isinstance(result["skill_hits"], list)
    assert result["connection_summary"]


def test_matching_fingerprint_changes_when_algorithm_changes():
    profile = {"skills": ["Python"]}
    cfg = {"title_keywords": ["backend"]}

    first = score_fingerprint(profile, cfg)
    assert len(first) == 40

    # The algorithm version is part of the fingerprint, so changing the
    # scorer version invalidates existing cached scores.
    from app.matching import scorer

    original = scorer.MATCHING_ALGORITHM_VERSION
    scorer.MATCHING_ALGORITHM_VERSION = "test-next"
    try:
        second = score_fingerprint(profile, cfg)
    finally:
        scorer.MATCHING_ALGORITHM_VERSION = original

    assert first != second


def test_cached_score_contains_machine_readable_breakdown():
    profile = {"skills": ["Python"]}
    cfg = {"title_keywords": ["backend"]}
    job = {
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Python APIs",
        "url": "https://example.com/jobs/1",
        "salary": "",
    }

    score, reasons, breakdown = score_listing_cached(type("Listing", (), job)(), profile, cfg)

    parsed = json.loads(breakdown)
    assert parsed["algorithm_version"] == MATCHING_ALGORITHM_VERSION
    assert parsed["score"] == score
    assert json.loads(reasons) == parsed["reasons"]
