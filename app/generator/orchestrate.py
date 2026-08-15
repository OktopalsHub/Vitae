"""Orchestration for apply-assist copy generation.

Coordinates cover letter + application answer generation.
Handles stale copy detection and single-answer rewrites.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.llm import LLMCreds
from app.generator.apply_copy import (
    generate_application_answers,
    generate_cover_blurb,
    rewrite_single_answer,
    template_application_answers,
)

JobLike = Any
logger = logging.getLogger(__name__)

# Old template voice — regenerate instead of serving it again.
_STALE_COPY_MARKERS = (
    "because the jd focuses on",
    "that matches work i already do",
    "own features from design to production",
    "this work maps to",
)

_APPLY_PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "profile" / "apply_profile.json"


def default_apply_profile() -> dict[str, Any]:
    """Default apply profile structure."""
    return {
        "full_name": "",
        "email": "",
        "phone": "",
        "linkedin": "",
        "github": "",
        "location_preference": "",
        "website": "",
        "note": "",
        "years_experience": "",
        "work_authorization": "",
        "salary_expectation": "",
        "earliest_start": "",
        "career_facts": [],
        "experience_highlights": [],
        "skills": [],
        "summary": "",
    }


def load_apply_profile() -> dict[str, Any]:
    import json
    base = default_apply_profile()
    if _APPLY_PROFILE_PATH.exists():
        try:
            stored = json.loads(_APPLY_PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                base.update({k: v for k, v in stored.items() if v is not None})
        except json.JSONDecodeError:
            pass
    return base


def apply_assist_payload(job: JobLike, apply_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.generator.cv_tailor import assert_download_under_user, list_resume_files

    profile = apply_profile if apply_profile is not None else {
        "full_name": "",
        "email": "",
        "phone": "",
        "linkedin": "",
        "github": "",
        "website": "",
        "location_preference": "",
        "years_experience": "",
        "work_authorization": "",
        "salary_expectation": "",
        "earliest_start": "",
    }
    output_dir = getattr(job, "output_dir", None)
    try:
        assert_download_under_user(output_dir)
        resume_files = list_resume_files(output_dir)
    except (ValueError, TypeError):
        resume_files = []
    return {
        "profile": profile,
        "resume_files": resume_files,
        "has_cv": bool(resume_files),
        "chips": [
            {"label": "Full name", "value": profile.get("full_name") or ""},
            {"label": "Email", "value": profile.get("email") or ""},
            {"label": "Phone", "value": profile.get("phone") or ""},
            {"label": "LinkedIn", "value": profile.get("linkedin") or ""},
            {"label": "GitHub", "value": profile.get("github") or ""},
            {"label": "Location", "value": profile.get("location_preference") or ""},
            {"label": "Website", "value": profile.get("website") or ""},
            {"label": "Years experience", "value": profile.get("years_experience") or ""},
            {"label": "Work authorization", "value": profile.get("work_authorization") or ""},
            {"label": "Salary expectation", "value": profile.get("salary_expectation") or ""},
            {"label": "Earliest start", "value": profile.get("earliest_start") or ""},
        ],
    }


async def ensure_apply_copy(
    job: JobLike,
    apply_profile: dict[str, Any] | None = None,
    *,
    force_cover: bool = False,
    force_answers: bool = False,
    creds: LLMCreds | None = None,
    existing: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Generate cover/answers. Persistence is owned by the caller (DB)."""
    del user_id
    apply_profile = apply_profile or load_apply_profile()
    draft = existing if isinstance(existing, dict) else {}
    cover = str(draft.get("cover_blurb") or "").strip()
    answers = draft.get("answers") if isinstance(draft.get("answers"), list) else []
    blob = " ".join(
        [cover]
        + [str(a.get("answer") or "") for a in answers if isinstance(a, dict)]
    ).lower()
    if any(marker in blob for marker in _STALE_COPY_MARKERS):
        force_cover = True
        force_answers = True

    if force_cover or not cover:
        cover = await generate_cover_blurb(
            job,
            apply_profile,
            previous=cover,
            rewrite=force_cover and bool(cover),
            creds=creds,
        )
    if force_answers or not answers:
        answers = await generate_application_answers(
            job,
            apply_profile,
            rewrite=force_answers and bool(answers),
            creds=creds,
        )
    return {"cover_blurb": cover, "answers": answers}


async def rewrite_answer_in_draft(
    job: JobLike,
    question: str,
    apply_profile: dict[str, Any] | None = None,
    creds: LLMCreds | None = None,
    existing: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Rewrite one answer. Persistence is owned by the caller (DB)."""
    del user_id
    apply_profile = apply_profile or load_apply_profile()
    copy = await ensure_apply_copy(job, apply_profile, creds=creds, existing=existing)
    answers = list(copy["answers"])
    question = (question or "").strip()
    previous = ""
    idx = -1
    for i, item in enumerate(answers):
        if str(item.get("question") or "").strip().lower() == question.lower():
            idx = i
            previous = str(item.get("answer") or "")
            break
    rewritten = await rewrite_single_answer(job, apply_profile, question, previous, creds=creds)
    if idx >= 0:
        answers[idx] = {"question": answers[idx]["question"], "answer": rewritten}
    else:
        answers.append({"question": question, "answer": rewritten})
    return {"cover_blurb": copy["cover_blurb"], "answers": answers}
