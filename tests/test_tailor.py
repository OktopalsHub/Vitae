from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.apply_assist import (
    template_about_yourself,
    template_application_answers,
    template_cover_blurb,
)
from app.tailor.generator import _fallback_resume, build_tailored_content, generate_resume_files


def test_fallback_resume_uses_only_profile_skills():
    profile = {
        "name": "Ada Lovelace",
        "summary": "Pioneer mathematician and programmer",
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "experience_raw": [],
        "projects_raw": [],
        "education_raw": [],
    }
    job = SimpleNamespace(
        title="Engineer",
        company="Acme",
        description="Need Node.js and TypeScript experts",
        location="Remote",
        id=1,
        url="",
    )
    data = _fallback_resume(profile, job)
    blob = json.dumps(data).lower()
    assert "pioneer mathematician" in data["summary"].lower()
    assert "node.js" not in blob
    assert "miva" not in blob
    assert "4 years" not in data["summary"].lower()
    skill_text = " ".join(g["value"] for g in data["skills"]).lower()
    assert "python" in skill_text
    assert data["education"] == []


@pytest.mark.asyncio
async def test_build_tailored_content_falls_back_when_llm_fails(tmp_path):
    profile = {
        "name": "Ada Lovelace",
        "contact": "ada@example.com",
        "summary": "Pioneer mathematician",
        "skills": ["Mathematics", "Programming"],
        "experience_raw": ["Analytical Engine — designed algorithms"],
        "projects_raw": [],
        "education_raw": ["University of London"],
    }
    job = SimpleNamespace(
        id=1,
        title="Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/1",
        description="Need a strong engineer with APIs and Python.",
        output_dir=None,
    )
    with patch(
        "app.tailor.generator.has_llm",
        return_value=True,
    ), patch(
        "app.tailor.generator._llm_complete",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        data, used_fallback = await build_tailored_content(job, profile=profile, creds=None)
    assert used_fallback is True
    assert data.get("summary")
    assert isinstance(data.get("skills"), list)


@pytest.mark.asyncio
async def test_generate_resume_files_writes_pdf_docx(tmp_path, monkeypatch):
    from app.tailor import generator as gen

    out_root = tmp_path / "out"
    out_root.mkdir()

    def fake_output_dir(job, user_id=None, profile_id=None):
        out_root.mkdir(parents=True, exist_ok=True)
        return out_root

    monkeypatch.setattr(gen, "output_dir_for", fake_output_dir)

    profile = {
        "name": "Ada Lovelace",
        "contact": "ada@example.com",
        "summary": "Pioneer mathematician",
        "skills": ["Mathematics"],
        "experience_raw": ["Analytical Engine — designed algorithms"],
        "projects_raw": [],
        "education_raw": [],
    }
    job = SimpleNamespace(
        id=42,
        title="Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/42",
        description="Python APIs",
        output_dir=None,
    )
    with patch("app.tailor.generator.has_llm", return_value=False):
        out, used_fallback = await generate_resume_files(
            job, profile=profile, display_name="Ada Lovelace"
        )
    assert used_fallback is True
    assert out == out_root
    names = {p.name for p in out.iterdir()}
    assert any(n.endswith(".pdf") for n in names)
    assert any(n.endswith(".docx") for n in names)


def test_apply_templates_use_profile_not_hardcoded_author():
    job = SimpleNamespace(
        title="Backend Engineer",
        company="Globex",
        description="Node.js TypeScript",
        location="Remote",
        id=1,
    )
    profile = {
        "full_name": "Ada Lovelace",
        "years_experience": "5",
        "skills": ["Python"],
        "career_facts": ["Built pioneering software for the Analytical Engine"],
        "experience_highlights": ["Analytical Engine — algorithms"],
        "summary": "",
    }
    cover = template_cover_blurb(job, profile)
    about = template_about_yourself(job, profile)
    assert "Ada Lovelace" in cover
    assert "Ada Lovelace" in about
    assert "Daniel" not in about
    assert "Metaverse" not in about
    assert "Teamlyf" not in about
    assert "Analytical Engine" in about
    answers = template_application_answers(job, profile)
    why = next(a["answer"] for a in answers if "why this" in a["question"].lower())
    blob = f"{cover}\n{about}\n{why}".lower()
    assert "jd focuses" not in blob
    assert "that matches work i already do" not in blob
    assert "own features from design to production" not in blob
    assert "globex" in why.lower()
