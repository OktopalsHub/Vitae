"""Tailored CV generation — resume rewriting, PDF/DOCX export.

Orchestrates LLM-powered resume tailoring with rule-based fallbacks.
Prompt templates live in cv_prompts.py.
Text helpers and fallbacks live in cv_helpers.py.
Export writers live in cv_export.py.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from slugify import slugify

from app.config import project_path
from app.llm import LLMCreds, has_llm, llm_complete
from app.profile.loader import load_or_build_profile
from app.generator.cv_prompts import SYSTEM_PROMPT
from app.generator.cv_helpers import (
    fallback_resume,
    post_validate_resume,
    extract_json,
)
from app.generator.cv_export import (
    resume_to_markdown,
    write_docx,
    write_pdf,
)

JobLike = Any
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------

async def _llm_complete(prompt: str, creds: LLMCreds | None = None) -> str:
    return await llm_complete(
        prompt=prompt,
        system=SYSTEM_PROMPT,
        json_mode=True,
        max_tokens=4000,
        temperature=0.3,
        creds=creds,
    )


# ---------------------------------------------------------------------------
# LLM prompt builder
# ---------------------------------------------------------------------------

async def build_tailored_content(
    job: JobLike,
    profile: dict[str, Any] | None = None,
    creds: LLMCreds | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return (resume_data, used_fallback)."""
    import json
    profile = profile or load_or_build_profile()
    if not has_llm(creds):
        return post_validate_resume(fallback_resume(profile, job), job), True

    profile_context = json.dumps(
        {k: profile[k] for k in (
            "name", "contact", "summary", "skills",
            "experience_raw", "projects_raw", "education_raw",
        ) if k in profile},
        indent=2,
    )[:14000]

    jd_text = (job.description or "")[:8000]
    prompt = (
        f"CANDIDATE PROFILE (source of truth — do not add anything not in this data):\n"
        f"{profile_context}\n\n"
        f"TARGET JOB:\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"URL: {job.url}\n"
        f"=== BEGIN JOB DESCRIPTION (UNTRUSTED) ===\n"
        f"{jd_text}\n"
        f"=== END JOB DESCRIPTION ===\n\n"
        f"Create a tailored resume for THIS job only.\n\n"
        f"RULES:\n"
        f"1) Summary (3-4 sentences): name the role/domain from the JD, then map\n"
        f"   2-3 JD requirements to real strengths from the profile. No hype.\n"
        f"2) Skills: prioritize stacks the JD asks for that exist in the profile.\n"
        f"   Do NOT add skills the profile does not have.\n"
        f"3) Experience bullets: lead with work matching JD duties/stack. Demote\n"
        f"   or drop weak matches. One idea per bullet. Quantify only when the\n"
        f"   profile already has the number.\n"
        f"4) Projects: keep only projects that reinforce JD fit.\n"
        f"5) If the JD asks for something the profile doesn't have, leave it out.\n"
        f"   Do NOT fabricate experience.\n"
    )
    try:
        raw = await _llm_complete(prompt, creds=creds)
        data = extract_json(raw)
        if not data.get("summary") or not data.get("experiences"):
            raise ValueError("Incomplete LLM resume")
        return post_validate_resume(data, job), False
    except Exception as exc:
        logger.warning("LLM CV tailor failed; using rule-based fallback: %s", exc)
        return post_validate_resume(fallback_resume(profile, job), job), True


# ---------------------------------------------------------------------------
# Output path management
# ---------------------------------------------------------------------------

def output_dir_for(
    job: JobLike,
    user_id: str | None = None,
    *,
    profile_id: int | None = None,
) -> Path:
    company = slugify(job.company or "company")[:40] or "company"
    role = slugify(job.title or "role")[:50] or "role"
    if not user_id:
        raise ValueError("user_id is required for resume output paths")
    if profile_id is not None:
        folder = project_path(
            "data",
            "users",
            str(user_id),
            "profiles",
            str(profile_id),
            "outputs",
            f"{job.id}-{company}-{role}",
        )
    else:
        folder = project_path(
            "data", "users", str(user_id), "outputs", f"{job.id}-{company}-{role}"
        )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_filename_part(value: str, fallback: str, max_len: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:max_len] or fallback).rstrip(" .")


def resume_basename(job: JobLike, display_name: str | None = None) -> str:
    """Human download name: Name - Role - Company."""
    name = _safe_filename_part(display_name or "Candidate", "Candidate")
    role = _safe_filename_part(job.title or "", "Role")
    company = _safe_filename_part(job.company or "", "Company")
    return f"{name} - {role} - {company}"


_USERS_ROOT = project_path("data", "users")


def assert_download_under_user(output_dir: str | Path | None) -> Path:
    """Resolve *output_dir* and assert it stays inside the per-user data tree."""
    if not output_dir:
        raise ValueError("output_dir is required")
    resolved = Path(output_dir).resolve()
    users_root = _USERS_ROOT.resolve()
    if not str(resolved).startswith(str(users_root)):
        raise ValueError(
            f"output_dir {str(output_dir)!r} is outside the allowed user data directory"
        )
    return resolved


def list_resume_files(output_dir: str | Path | None) -> list[str]:
    if not output_dir:
        return []
    try:
        out = assert_download_under_user(output_dir)
    except ValueError:
        return []
    if not out.exists():
        return []
    return sorted(
        p.name
        for p in out.iterdir()
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx"}
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def generate_resume_files(
    job: JobLike,
    *,
    profile: dict[str, Any] | None = None,
    creds: LLMCreds | None = None,
    user_id: str | None = None,
    profile_id: int | None = None,
    display_name: str | None = None,
) -> tuple[Path, bool]:
    """Write PDF/DOCX exports. Returns (output_dir, used_fallback)."""
    profile = profile or load_or_build_profile()
    name = display_name or profile.get("name") or "Candidate"
    contact = profile.get("contact") or ""
    data, used_fallback = await build_tailored_content(job, profile=profile, creds=creds)
    out = output_dir_for(job, user_id=user_id, profile_id=profile_id)

    for old in out.iterdir():
        if old.is_file():
            old.unlink(missing_ok=True)

    base = resume_basename(job, display_name=name)
    write_docx(out / f"{base}.docx", name, contact, data)
    write_pdf(out / f"{base}.pdf", name, contact, data)

    return out, used_fallback
