"""Apply copy template fallbacks — deterministic text when no LLM is available.

These produce ASD-STE100 compliant copy from profile data alone.
Used as fallback when LLM keys are not configured.
"""

from __future__ import annotations

from typing import Any

from app.generator.apply_prompts import ASD_STE100_RULES

JobLike = Any


# ---------------------------------------------------------------------------
# Profile context helpers (shared with apply_copy.py)
# ---------------------------------------------------------------------------

def candidate_name(apply_profile: dict[str, Any]) -> str:
    return (apply_profile.get("full_name") or "").strip() or "I"


def career_facts(apply_profile: dict[str, Any]) -> list[str]:
    facts = apply_profile.get("career_facts") or []
    if isinstance(facts, list):
        return [str(f).strip() for f in facts if str(f).strip()]
    return []


def experience_highlights(apply_profile: dict[str, Any]) -> list[str]:
    lines = apply_profile.get("experience_highlights") or []
    if isinstance(lines, list):
        return [str(x).strip() for x in lines if str(x).strip()]
    return []


def skills_csv(apply_profile: dict[str, Any], limit: int = 30) -> str:
    skills = apply_profile.get("skills") or []
    if isinstance(skills, list):
        parts = [str(s).strip() for s in skills if str(s).strip()]
        return ", ".join(parts[:limit])
    return ""


def full_profile_context(apply_profile: dict[str, Any]) -> str:
    """Build the complete candidate context block for the LLM."""
    facts = career_facts(apply_profile)
    highlights = experience_highlights(apply_profile)
    skills = skills_csv(apply_profile, 50)
    summary = (apply_profile.get("summary") or "").strip()

    facts_block = "\n".join(f"  - {f}" for f in facts) if facts else "  (none)"
    exp_block = "\n".join(f"  {h}" for h in highlights) if highlights else "  (none)"

    return (
        "=== CANDIDATE PROFILE (TRUSTED SOURCE) ===\n"
        f"Name: {apply_profile.get('full_name') or '(not provided)'}\n"
        f"Years experience: {apply_profile.get('years_experience') or '(not provided)'}\n"
        f"Location preference: {apply_profile.get('location_preference') or '(not provided)'}\n"
        f"Work authorization: {apply_profile.get('work_authorization') or '(not provided)'}\n"
        f"Salary expectation: {apply_profile.get('salary_expectation') or '(not provided)'}\n"
        f"Earliest start: {apply_profile.get('earliest_start') or '(not provided)'}\n\n"
        f"Professional summary:\n  {summary or '(none)'}\n\n"
        f"Skills:\n  {skills or '(none)'}\n\n"
        f"Career facts:\n{facts_block}\n\n"
        f"Experience highlights (from CV):\n{exp_block}\n"
        "=== END CANDIDATE PROFILE ==="
    )


def jd_analysis(job: JobLike) -> str:
    from app.generator.apply_prompts import wrap_jd
    desc = (job.description or "").strip()
    if not desc:
        return "(no job description provided)"
    return wrap_jd(desc, max_chars=10000)


def job_meta(job: JobLike) -> str:
    parts = []
    if job.title:
        parts.append(f"Title: {job.title}")
    if job.company:
        parts.append(f"Company: {job.company}")
    if job.location:
        parts.append(f"Location: {job.location}")
    return "\n".join(parts) if parts else "(no metadata)"


# ---------------------------------------------------------------------------
# Template (fallback) generators — ASD-STE100 compliant
# ---------------------------------------------------------------------------

def _first_evidence(apply_profile: dict[str, Any]) -> str:
    facts = career_facts(apply_profile)
    if facts:
        text = facts[0]
        return text if text.endswith((".", "!", "?")) else f"{text}."
    highlights = experience_highlights(apply_profile)
    if highlights:
        text = highlights[0]
        return text if text.endswith((".", "!", "?")) else f"{text}."
    summary = (apply_profile.get("summary") or "").strip()
    if summary:
        return summary if summary.endswith((".", "!", "?")) else f"{summary}."
    return ""


def template_about_yourself(job: JobLike, apply_profile: dict[str, Any]) -> str:
    """Past → Present → Future structure. ASD-STE100 voice."""
    name = candidate_name(apply_profile)
    company = job.company or "this team"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip()

    facts = career_facts(apply_profile)
    highlights = experience_highlights(apply_profile)

    evidence_parts = []
    if facts:
        evidence_parts.extend(facts[:2])
    elif highlights:
        evidence_parts.extend(highlights[:2])

    evidence_text = ""
    if evidence_parts:
        parts = []
        for f in evidence_parts:
            parts.append(f if f.endswith((".", "!", "?")) else f"{f}.")
        evidence_text = " ".join(parts)

    years_bit = f"{name} has {years} years of experience in production systems." if years else ""
    past = years_bit or f"{name} has shipped production systems for several years."
    if evidence_text:
        past = f"{years_bit} {evidence_text}" if years_bit else evidence_text
    past = past.strip()

    present = f"{name} now builds and maintains production systems with real users."

    future = (
        f"{name} wants to join {company} as {title}. "
        f"{name} will bring the same approach to your team."
    )

    return f"{past} {present} {future}".replace("..", ".").strip()


def template_cover_blurb(job: JobLike, apply_profile: dict[str, Any]) -> str:
    """ASD-STE100 cover note. Short sentences. Active voice."""
    name = candidate_name(apply_profile)
    company = job.company or "your team"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip()
    years_bit = f"I have {years} years of experience." if years else ""
    evidence = _first_evidence(apply_profile)
    evidence_bit = f" {evidence}" if evidence else ""
    return (
        f"I am {name}.{f' {years_bit}' if years_bit else ''}{evidence_bit}\n\n"
        f"The {title} role at {company} fits this background. "
        f"I build and own production systems from design through launch. "
        f"My resume has more detail.\n\n"
        f"I am available to discuss relevant examples."
    )


def template_relevant_experience(job: JobLike, apply_profile: dict[str, Any]) -> str:
    """ASD-STE100 relevant experience. Map JD needs to profile facts."""
    company = job.company or "this company"
    title = job.title or "this role"
    facts = career_facts(apply_profile)[:2]
    highlights = experience_highlights(apply_profile)[:2]
    if facts:
        evidence = " ".join(
            f if f.endswith((".", "!", "?")) else f"{f}." for f in facts
        )
    elif highlights:
        evidence = " ".join(
            h if h.endswith((".", "!", "?")) else f"{h}." for h in highlights
        )
    else:
        years = (apply_profile.get("years_experience") or "").strip()
        skills = skills_csv(apply_profile, 6)
        years_bit = f"I have {years} years of" if years else "I have"
        skills_bit = f" including {skills}," if skills else ""
        evidence = (
            f"{years_bit} hands-on delivery of production systems{skills_bit} "
            f"working with product and engineering through release."
        )
        if not evidence.endswith("."):
            evidence += "."
    return (
        f"The {title} role at {company} needs production-system experience. "
        f"{evidence} "
        f"I design, build, and maintain systems that ship to real users."
    )


def template_application_answers(job: JobLike, apply_profile: dict[str, Any]) -> list[dict[str, str]]:
    """All 9 standard answers. ASD-STE100 voice. Past→Present→Future for 'about yourself'."""
    company = job.company or "this company"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip() or "several"
    facts = career_facts(apply_profile)
    highlights = experience_highlights(apply_profile)
    if facts:
        achievement = facts[0]
    elif highlights:
        achievement = highlights[0]
    else:
        achievement = (
            "I delivered production features end-to-end with product and engineering. "
            "I stayed responsible after release."
        )
    evidence = _first_evidence(apply_profile)
    evidence_bit = f" {evidence}" if evidence else ""
    why = (
        f"{company} builds a product that needs a {title}. "
        f"{evidence_bit} "
        f"This work fits what I do. I build and maintain production systems."
    )
    return [
        {"question": "Tell us about yourself", "answer": template_about_yourself(job, apply_profile)},
        {"question": "Why do you want this role / Why this company?", "answer": why},
        {"question": "Relevant experience / What makes you a fit?", "answer": template_relevant_experience(job, apply_profile)},
        {"question": "Years of experience", "answer": f"{years} years of professional experience."},
        {"question": "Biggest achievement", "answer": achievement},
        {
            "question": "Work authorization / Location",
            "answer": apply_profile.get("work_authorization") or apply_profile.get("location_preference") or "Available for remote work.",
        },
        {
            "question": "Salary expectation",
            "answer": apply_profile.get("salary_expectation") or "Open to discussion based on role and location.",
        },
        {"question": "Earliest start date", "answer": apply_profile.get("earliest_start") or "2-4 weeks."},
        {
            "question": "Notice period / Availability",
            "answer": (
                f"I can start in {apply_profile.get('earliest_start') or '2-4 weeks'}. "
                f"I am flexible for remote interviews."
            ),
        },
    ]
