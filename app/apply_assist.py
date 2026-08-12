from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import project_path
from app.llm import LLMCreds, has_llm, llm_complete

# Duck-typed job objects (JobListing / JobCard) — needs .title/.company/.location/.description
JobLike = Any

logger = logging.getLogger(__name__)

APPLY_PROFILE_PATH = project_path("data", "profile", "apply_profile.json")
DRAFTS_DIR = project_path("data", "apply_drafts")
_MIN_LLM_ANSWERS = 5


def _draft_path(job_id: int, user_id: str | None = None) -> Path:
    if user_id:
        path = project_path("data", "users", str(user_id), "drafts")
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{int(job_id)}.json"
    return DRAFTS_DIR / f"{int(job_id)}.json"


def load_job_draft(job_id: int, user_id: str | None = None) -> dict[str, Any]:
    path = _draft_path(job_id, user_id=user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_job_draft(
    job_id: int, data: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    if not user_id:
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    current = load_job_draft(job_id, user_id=user_id)
    current.update(data)
    _draft_path(job_id, user_id=user_id).write_text(
        json.dumps(current, indent=2), encoding="utf-8"
    )
    return current

def _parse_contact(contact: str) -> dict[str, str]:
    email = ""
    phone = ""
    linkedin = ""
    github = ""
    for part in re.split(r"[|]", contact or ""):
        p = part.strip()
        if "@" in p and not email:
            email = p.replace(" ", "")
        elif re.search(r"\+?\d[\d\s-]{7,}", p) and not phone:
            phone = re.sub(r"\s+", "", p)
        elif "linkedin" in p.lower() and not linkedin:
            linkedin = p if p.startswith("http") else f"https://{p.lstrip('/')}"
        elif "github" in p.lower() and not github:
            github = p if p.startswith("http") else f"https://{p.lstrip('/')}"
    return {"email": email, "phone": phone, "linkedin": linkedin, "github": github}


def default_apply_profile() -> dict[str, Any]:
    """Legacy shared-profile defaults for single-user smoke scripts only."""
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
        "salary_expectation": "Open to discussion based on role and location",
        "earliest_start": "2–4 weeks",
        "career_facts": [],
        "experience_highlights": [],
        "skills": [],
        "summary": "",
    }


def _candidate_name(apply_profile: dict[str, Any]) -> str:
    return (apply_profile.get("full_name") or "").strip() or "I"


def _career_facts(apply_profile: dict[str, Any]) -> list[str]:
    facts = apply_profile.get("career_facts") or []
    if isinstance(facts, list):
        return [str(f).strip() for f in facts if str(f).strip()]
    return []


def _experience_highlights(apply_profile: dict[str, Any]) -> list[str]:
    lines = apply_profile.get("experience_highlights") or []
    if isinstance(lines, list):
        return [str(x).strip() for x in lines if str(x).strip()]
    return []


def _skills_csv(apply_profile: dict[str, Any], limit: int = 12) -> str:
    skills = apply_profile.get("skills") or []
    if isinstance(skills, list):
        parts = [str(s).strip() for s in skills if str(s).strip()]
        return ", ".join(parts[:limit])
    return ""


def load_apply_profile() -> dict[str, Any]:
    base = default_apply_profile()
    if APPLY_PROFILE_PATH.exists():
        try:
            stored = json.loads(APPLY_PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                base.update({k: v for k, v in stored.items() if v is not None})
        except json.JSONDecodeError:
            pass
    return base


def save_apply_profile(data: dict[str, Any]) -> dict[str, Any]:
    current = load_apply_profile()
    for key in (
        "full_name",
        "email",
        "phone",
        "linkedin",
        "github",
        "location_preference",
        "website",
        "note",
        "years_experience",
        "work_authorization",
        "salary_expectation",
        "earliest_start",
    ):
        if key in data:
            current[key] = (data.get(key) or "").strip()
    APPLY_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPLY_PROFILE_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def template_cover_blurb(job: JobLike, apply_profile: dict[str, Any]) -> str:
    name = _candidate_name(apply_profile)
    company = job.company or "your team"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip()
    years_bit = f" with {years} years of experience" if years else ""
    themes = _jd_theme_hits(job, 4)
    theme_bit = (
        f" The role asks for {', '.join(themes)}. "
        if themes
        else " "
    )
    skills = _skills_csv(apply_profile, 6)
    skills_bit = f"My background includes {skills}." if skills else ""
    facts = _career_facts(apply_profile)[:1]
    evidence = f" {facts[0]}" if facts else ""
    if evidence and not evidence.endswith((".", "!", "?")):
        evidence += "."
    return (
        f"Hello,\n\n"
        f"I am {name}{years_bit}.{theme_bit}{skills_bit}{evidence} "
        f"I want to bring that experience to the {title} role at {company}.\n\n"
        f"I attached my resume. I am happy to share more details.\n\n"
        f"Best regards,\n{name}"
    )


def template_about_yourself(job: JobLike, apply_profile: dict[str, Any]) -> str:
    name = _candidate_name(apply_profile)
    company = job.company or "your team"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip()
    years_bit = f"Over {years} years, " if years else ""
    facts = _career_facts(apply_profile)[:3]
    highlights = _experience_highlights(apply_profile)[:4]
    skills = _skills_csv(apply_profile, 8)
    body_parts: list[str] = [f"I am {name}."]
    if facts:
        joined = " ".join(facts)
        if not joined.endswith((".", "!", "?")):
            joined += "."
        body_parts.append(joined)
    elif highlights:
        body_parts.append("Recent experience includes: " + "; ".join(highlights) + ".")
    elif apply_profile.get("summary"):
        body_parts.append(str(apply_profile["summary"]).strip())
    themes = _jd_theme_hits(job, 4)
    theme_bit = (
        f" This work maps to {', '.join(themes)} needed for this role."
        if themes
        else ""
    )
    skills_bit = f" Core skills include {skills}." if skills else ""
    experience_bit = (
        f"{years_bit}I build production systems and work with product and engineering teams."
        f"{skills_bit}{theme_bit}"
    )
    closing = (
        f"I want the {title} role at {company} because it matches that experience. "
        f"I can own delivery and help {company} ship reliable product work."
    )
    return " ".join(body_parts + [experience_bit, closing])


def _strip_timeline_labels(text: str) -> str:
    """Remove Present:/Past:/Future: labels if a model adds them to spoken answers."""
    cleaned = re.sub(
        r"(?im)^\s*(present|past|future)\s*:\s*",
        "",
        text or "",
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _job_description(job: JobLike, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", (job.description or "").strip())[:limit]


def _jd_theme_hits(job: JobLike, limit: int = 8) -> list[str]:
    """Pull concrete requirement-ish phrases from the JD for template answers."""
    text = _job_description(job, 6000).lower()
    if not text:
        return []
    catalog = [
        ("api", "REST/API design"),
        ("typescript", "TypeScript"),
        ("node", "Node.js"),
        ("nestjs", "NestJS"),
        ("microservice", "microservices"),
        ("distributed", "distributed systems"),
        ("postgresql", "PostgreSQL"),
        ("postgres", "PostgreSQL"),
        ("mysql", "MySQL"),
        ("mongodb", "MongoDB"),
        ("redis", "Redis"),
        ("docker", "Docker"),
        ("kubernetes", "Kubernetes"),
        ("ci/cd", "CI/CD"),
        ("observability", "observability"),
        ("opentelemetry", "OpenTelemetry"),
        ("websocket", "real-time/WebSockets"),
        ("real-time", "real-time systems"),
        ("auth", "auth/security"),
        ("rbac", "RBAC"),
        ("payment", "payments"),
        ("saas", "multi-tenant SaaS"),
        ("multi-tenant", "multi-tenant SaaS"),
        ("ai", "AI systems"),
        ("llm", "LLM/AI features"),
        ("langgraph", "LangGraph/agent workflows"),
        ("python", "Python"),
        ("fastapi", "FastAPI"),
        ("aws", "AWS/cloud"),
        ("platform", "platform engineering"),
        ("reliability", "reliability"),
        ("scalability", "scalability"),
    ]
    hits: list[str] = []
    seen: set[str] = set()
    for needle, label in catalog:
        if needle in text and label.lower() not in seen:
            seen.add(label.lower())
            hits.append(label)
        if len(hits) >= limit:
            break
    return hits


def _career_context_blob(apply_profile: dict[str, Any]) -> str:
    """Build LLM context from the caller's per-user profile — never shared legacy CV data."""
    facts = _career_facts(apply_profile)
    highlights = _experience_highlights(apply_profile)
    skills = _skills_csv(apply_profile, 30)
    facts_block = "\n".join(f"- {f}" for f in facts) if facts else "(none provided)"
    exp_block = "\n".join(highlights[:40]) if highlights else "(none provided)"
    summary = (apply_profile.get("summary") or "").strip()
    return (
        f"Career facts:\n{facts_block}\n"
        f"Summary: {summary or '(none)'}\n"
        f"Skills: {skills or '(none)'}\n"
        f"Experience lines (from CV):\n{exp_block[:3500]}"
    )


def template_relevant_experience(job: JobLike, apply_profile: dict[str, Any]) -> str:
    company = job.company or "this company"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip()
    years_bit = f"{years} years of " if years else ""
    themes = _jd_theme_hits(job)
    theme_clause = (
        f"This {title} role at {company} calls for {', '.join(themes[:5])}. "
        if themes
        else f"For the {title} role at {company}, "
    )
    facts = _career_facts(apply_profile)[:2]
    highlights = _experience_highlights(apply_profile)[:3]
    if facts:
        evidence = " ".join(facts)
    elif highlights:
        evidence = "Relevant experience includes: " + "; ".join(highlights) + "."
    else:
        skills = _skills_csv(apply_profile, 8)
        evidence = (
            f"I bring {years_bit}hands-on experience delivering production systems"
            + (f" with {skills}" if skills else "")
            + "."
        )
    return (
        f"{theme_clause}{evidence} "
        f"That maps directly to what this role needs."
    )


def template_application_answers(job: JobLike, apply_profile: dict[str, Any]) -> list[dict[str, str]]:
    company = job.company or "this company"
    title = job.title or "this role"
    years = (apply_profile.get("years_experience") or "").strip() or "several"
    facts = _career_facts(apply_profile)
    highlights = _experience_highlights(apply_profile)
    themes = _jd_theme_hits(job, 3)
    if facts:
        achievement = facts[0]
    elif highlights:
        achievement = highlights[0]
    else:
        achievement = (
            "I have delivered production features end-to-end, collaborating with "
            "product and engineering to ship reliable user-facing systems."
        )
    if themes:
        why = (
            f"I want the {title} role at {company} because the JD focuses on "
            f"{', '.join(themes)}. That matches work I already do. "
            f"I can contribute quickly and own features from design to production."
        )
    else:
        why = (
            f"I want the {title} role at {company} because it matches work I already do. "
            f"I can contribute quickly and own features from design to production."
        )
    return [
        {
            "question": "Tell us about yourself",
            "answer": template_about_yourself(job, apply_profile),
        },
        {
            "question": "Why do you want this role / Why this company?",
            "answer": why,
        },
        {
            "question": "Relevant experience / What makes you a fit?",
            "answer": template_relevant_experience(job, apply_profile),
        },
        {
            "question": "Years of experience",
            "answer": f"{years} years of professional experience.",
        },
        {
            "question": "Biggest achievement",
            "answer": achievement,
        },
        {
            "question": "Work authorization / Location",
            "answer": apply_profile.get("work_authorization")
            or apply_profile.get("location_preference")
            or "Available for remote work.",
        },
        {
            "question": "Salary expectation",
            "answer": apply_profile.get("salary_expectation")
            or "Open to discussion based on role and location.",
        },
        {
            "question": "Earliest start date",
            "answer": apply_profile.get("earliest_start") or "2–4 weeks.",
        },
        {
            "question": "Notice period / Availability",
            "answer": (
                f"I am available to start in {apply_profile.get('earliest_start') or '2–4 weeks'} "
                f"and can interview on a flexible remote schedule."
            ),
        },
    ]


APPLY_WRITING_SYSTEM = """You write job-application copy in ASD-STE100 Simplified Technical English.

ASD-STE100 voice (required):
- Short, active sentences. Prefer one idea per sentence.
- Use approved-style clear verbs: build, design, lead, deliver, fix, reduce, increase, own.
- Prefer concrete nouns (system, API, latency, users, revenue, team) over soft adjectives.
- Avoid nested clauses and filler transitions.

Hard bans (never write these unless they appear verbatim in the candidate profile):
- world-class, world class, best-in-class, cutting-edge, passionate, results-driven,
  synergistic, leverage (as fluff), leverage my skills, proven track record,
  seamless, robust solutions, dynamic individual, go-getter, thrives in,
  excited to leverage, highly motivated, detail-oriented team player.

Targeting rules (required — generic copy fails):
- Every long answer must map 2–4 concrete needs from THIS job description to real profile facts.
- Name technologies, products, domains, or outcomes from the JD when the profile supports them.
- If the profile lacks evidence for a JD need, skip that need — do not invent or stretch.
- Never invent employers, titles, metrics, or skills.
- Do not say "tailored resume" or "customized resume"; say "my resume" if needed.
"""


async def _llm_text(
    prompt: str,
    fallback: str,
    max_tokens: int = 1200,
    creds: LLMCreds | None = None,
    *,
    system: str = "",
    temperature: float = 0.4,
) -> str:
    if not has_llm(creds):
        return fallback
    try:
        text = await llm_complete(
            prompt=prompt,
            system=system or APPLY_WRITING_SYSTEM,
            json_mode=False,
            max_tokens=max_tokens,
            temperature=temperature,
            creds=creds,
        )
        return (text or "").strip() or fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM text generation failed; using fallback: %s", exc)
        return fallback


async def generate_cover_blurb(
    job: JobLike,
    apply_profile: dict[str, Any],
    *,
    previous: str = "",
    rewrite: bool = False,
    creds: LLMCreds | None = None,
) -> str:
    fallback = previous.strip() or template_cover_blurb(job, apply_profile)
    rewrite_bit = ""
    if rewrite and previous.strip():
        rewrite_bit = (
            "Rewrite from scratch in ASD-STE100 with a sharper JD-specific angle. "
            "Keep facts truthful. Do not reuse stock phrases from the previous note.\n"
            f"Previous cover note:\n{previous[:2000]}\n"
        )
    themes = ", ".join(_jd_theme_hits(job, 8)) or "(infer from the full description)"
    prompt = (
        "Write a short cover note for THIS role only (90–130 words). Use ASD-STE100.\n"
        "Structure:\n"
        "1) Who you are + exact role + company.\n"
        "2) Map 2–3 JD requirements to real profile experience "
        "(name stack/outcomes the JD asks for — only if supported).\n"
        "3) Why THIS company/product using domain language from the JD (not vague interest).\n"
        "4) Closing: resume attached + open to talk.\n"
        "No hype adjectives. No generic blurb that could fit any company.\n"
        f"{rewrite_bit}"
        f"JD themes to hit if supported by the profile: {themes}\n"
        f"Candidate:\n{json.dumps({k: v for k, v in apply_profile.items() if k != 'experience_highlights'}, indent=2)[:3500]}\n"
        f"{_career_context_blob(apply_profile)}\n"
        f"Job title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"FULL job description:\n{_job_description(job, 4500)}\n"
        "Return plain text only. No bullet list. No subject line."
    )
    return await _llm_text(prompt, fallback, max_tokens=500, creds=creds)


async def generate_application_answers(
    job: JobLike,
    apply_profile: dict[str, Any],
    *,
    rewrite: bool = False,
    creds: LLMCreds | None = None,
) -> list[dict[str, str]]:
    fallback = template_application_answers(job, apply_profile)
    if not has_llm(creds):
        return fallback

    rewrite_bit = (
        "Produce a fresh rewrite in ASD-STE100. Same question themes. "
        "Stronger JD targeting. No stock phrases.\n"
        if rewrite
        else ""
    )
    themes = ", ".join(_jd_theme_hits(job, 10)) or "(read the full JD carefully)"
    prompt = (
        "Create truthful ASD-STE100 answers for common application form questions for THIS job only.\n"
        "Return ONLY JSON: {\"answers\":[{\"question\":\"...\",\"answer\":\"...\"}]}\n"
        f"{rewrite_bit}"
        "Include these themes: about yourself, why this role/company, relevant experience, "
        "years of experience, biggest achievement, work authorization/location, "
        "salary expectation, earliest start / availability.\n\n"
        f"JD requirements/themes to prioritize (only if supported by profile): {themes}\n\n"
        "About yourself (100–160 words, ASD-STE100):\n"
        "- Lead with work most relevant to this JD — not a generic career bio.\n"
        "- Map 2 concrete profile facts to JD needs.\n"
        "- Close with why this role/company using JD product/domain language.\n"
        "- Never write Present/Past/Future labels.\n\n"
        "Why this role / company (70–110 words):\n"
        "- Name something specific from the JD (product, customers, stack, stage, problem).\n"
        "- Tie it to one real experience. No passion filler.\n\n"
        "Relevant experience / fit (100–160 words):\n"
        "- Pull 3 concrete JD requirements.\n"
        "- For each: employer/context from the profile + outcome (when known).\n"
        "- If you cannot map a requirement honestly, omit it.\n"
        "- Do not write a blurb that could fit any company.\n\n"
        "Biggest achievement: pick the achievement that best proves fitness for THIS role.\n"
        "Short answers (years, auth, salary, start) stay factual from the profile.\n\n"
        f"Candidate profile:\n{json.dumps({k: v for k, v in apply_profile.items() if k != 'experience_highlights'}, indent=2)[:3500]}\n"
        f"{_career_context_blob(apply_profile)}\n"
        f"Job title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"FULL job description:\n{_job_description(job, 5000)}\n"
    )
    raw = await _llm_text(prompt, "", max_tokens=2200, creds=creds, temperature=0.35)
    if not raw:
        return fallback
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        answers = data.get("answers") if isinstance(data, dict) else data
        cleaned: list[dict[str, str]] = []
        if isinstance(answers, list):
            for item in answers:
                if not isinstance(item, dict):
                    continue
                q = str(item.get("question") or "").strip()
                a = str(item.get("answer") or "").strip()
                if q and a:
                    if "about yourself" in q.lower():
                        a = _strip_timeline_labels(a)
                    cleaned.append({"question": q, "answer": a})
        if len(cleaned) < _MIN_LLM_ANSWERS:
            logger.warning(
                "LLM returned only %s application answers (need %s); using template fallback",
                len(cleaned),
                _MIN_LLM_ANSWERS,
            )
            return fallback
        return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse LLM application answers; using fallback: %s", exc)
        return fallback


async def rewrite_single_answer(
    job: JobLike,
    apply_profile: dict[str, Any],
    question: str,
    previous_answer: str = "",
    creds: LLMCreds | None = None,
) -> str:
    question = (question or "").strip()
    previous_answer = (previous_answer or "").strip()
    fallbacks = {
        item["question"].lower(): item["answer"]
        for item in template_application_answers(job, apply_profile)
    }
    fallback = previous_answer or fallbacks.get(question.lower()) or template_about_yourself(
        job, apply_profile
    )
    themes = ", ".join(_jd_theme_hits(job, 8))
    ql = question.lower()
    focus = (
        "Rewrite in ASD-STE100. Every claim must be checkable against the profile "
        "and useful for THIS JD. Drop empty adjectives and hype.\n"
    )
    if "about yourself" in ql:
        focus = (
            "ASD-STE100. Lead with experience most relevant to this JD. "
            "Map two profile facts to JD needs. Close with company/role fit. "
            "No Present/Past/Future labels.\n"
        )
    elif any(k in ql for k in ("why this", "why do you want", "why our", "company")):
        focus = (
            "ASD-STE100. Name something specific from the JD "
            "(product, users, stack, or problem). Tie it to one real experience. "
            "No passion filler.\n"
        )
    elif any(k in ql for k in ("relevant experience", "makes you a fit", "why are you a fit", "why you")):
        focus = (
            "ASD-STE100. Pull 2–4 concrete JD requirements and map each to profile "
            "experience (employer + outcome when known). Omit what you cannot support.\n"
        )
    prompt = (
        f"{focus}"
        f"Question: {question}\n"
        f"JD themes: {themes or 'see full description'}\n"
        f"Previous answer (improve targeting; do not lightly paraphrase fluff):\n{previous_answer[:2500]}\n"
        f"Candidate profile:\n{json.dumps({k: v for k, v in apply_profile.items() if k != 'experience_highlights'}, indent=2)[:3000]}\n"
        f"{_career_context_blob(apply_profile)}\n"
        f"Job title: {job.title}\nCompany: {job.company}\n"
        f"FULL job description:\n{_job_description(job, 5000)}\n"
        "Return plain text only."
    )
    text = await _llm_text(prompt, fallback, max_tokens=800, creds=creds)
    if "about yourself" in ql:
        return _strip_timeline_labels(text)
    return text


async def ensure_apply_copy(
    job: JobLike,
    apply_profile: dict[str, Any] | None = None,
    *,
    force_cover: bool = False,
    force_answers: bool = False,
    creds: LLMCreds | None = None,
    existing: dict[str, Any] | None = None,
    user_id: str | None = None,  # retained for call-site compatibility; unused
) -> dict[str, Any]:
    """Generate cover/answers. Persistence is owned by the caller (DB)."""
    del user_id  # unused — drafts are no longer file-backed here
    apply_profile = apply_profile or load_apply_profile()
    draft = existing if isinstance(existing, dict) else {}
    cover = str(draft.get("cover_blurb") or "").strip()
    answers = draft.get("answers") if isinstance(draft.get("answers"), list) else []

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
    user_id: str | None = None,  # retained for call-site compatibility; unused
) -> dict[str, Any]:
    """Rewrite one answer. Persistence is owned by the caller (DB)."""
    del user_id
    apply_profile = apply_profile or load_apply_profile()
    copy = await ensure_apply_copy(
        job, apply_profile, creds=creds, existing=existing
    )
    answers = list(copy["answers"])
    question = (question or "").strip()
    previous = ""
    idx = -1
    for i, item in enumerate(answers):
        if str(item.get("question") or "").strip().lower() == question.lower():
            idx = i
            previous = str(item.get("answer") or "")
            break
    rewritten = await rewrite_single_answer(
        job, apply_profile, question, previous, creds=creds
    )
    if idx >= 0:
        answers[idx] = {"question": answers[idx]["question"], "answer": rewritten}
    else:
        answers.append({"question": question, "answer": rewritten})
    return {"cover_blurb": copy["cover_blurb"], "answers": answers}

def apply_assist_payload(job: JobLike, apply_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.tailor.generator import list_resume_files

    # Prefer caller-supplied per-user profile; never fall back to shared file on multiuser paths.
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
    resume_files = list_resume_files(job.output_dir)
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
