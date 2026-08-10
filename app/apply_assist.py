from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import project_path
from app.llm import LLMCreds, has_llm, llm_complete
from app.profile.loader import load_or_build_profile

# Duck-typed job objects (JobListing / JobCard) — needs .title/.company/.location/.description
JobLike = Any

APPLY_PROFILE_PATH = project_path("data", "profile", "apply_profile.json")
DRAFTS_DIR = project_path("data", "apply_drafts")

# Truthful career facts for apply copy (kept in code so regenerations stay consistent).
CAREER_FACTS = [
    "Metaverse Magna: Game Studio backends powering 30+ games, ~2M sessions in under a year, 100k+ unique players daily.",
    "Teamlyf: multi-tenant NestJS SaaS (messaging, LiveKit calls, Drive, payments, HR, projects).",
    "Teamlyf AI: experimented with LangGraph to build a memory system for projects, related tasks, documents, and chats so tasks can be generated with context-aware descriptions.",
    "Core skills: Node.js, TypeScript, NestJS, REST APIs, PostgreSQL/MySQL/MongoDB/Redis, Docker, CI/CD, cloud.",
]


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
    profile = load_or_build_profile()
    parsed = _parse_contact(profile.get("contact") or "")
    return {
        "full_name": profile.get("name") or "Daniel Mbazu",
        "email": parsed.get("email") or "",
        "phone": parsed.get("phone") or "",
        "linkedin": parsed.get("linkedin") or "",
        "github": parsed.get("github") or "",
        "location_preference": "Remote / Nigeria / Worldwide",
        "website": "",
        "note": "",
        "years_experience": "4+",
        "work_authorization": "Eligible to work remotely from Nigeria",
        "salary_expectation": "Open to discussion based on role and location",
        "earliest_start": "2–4 weeks",
    }


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
    name = apply_profile.get("full_name") or "Daniel Mbazu"
    company = job.company or "your team"
    title = job.title or "Backend Engineer"
    return (
        f"Hello,\n\n"
        f"I am {name}, a Backend Engineer with more than 4 years of experience in Node.js and TypeScript. "
        f"I design, build, and maintain scalable REST APIs and production services. "
        f"I am interested in the {title} role at {company}. "
        f"I can own backend features from design to deployment and work clearly with product and frontend teams.\n\n"
        f"I attached my resume. I am happy to share more details.\n\n"
        f"Best regards,\n{name}"
    )


def template_about_yourself(job: JobLike, apply_profile: dict[str, Any]) -> str:
    name = apply_profile.get("full_name") or "Daniel Mbazu"
    company = job.company or "your team"
    title = job.title or "this role"
    years = apply_profile.get("years_experience") or "4+"
    # Structure: current role → background → why this role (no Present/Past/Future labels).
    return (
        f"I am {name}, a Backend Engineer currently at Metaverse Magna, where I build and "
        f"operate Node.js/TypeScript platforms that power live games at scale. In less than a year, "
        f"systems I contributed to helped ship 30+ games, drive about 2 million game sessions, and "
        f"support over 100k unique players daily. "
        f"Over {years} years I have designed multi-tenant SaaS backends, real-time and "
        f"matchmaking systems, payment webhook flows, and production observability with Docker, "
        f"CI/CD, and cloud infrastructure. That work taught me how to ship reliable APIs under load "
        f"and partner clearly with product and frontend teams. "
        f"I am excited about the {title} opportunity at {company} because it aligns with "
        f"my strengths in scalable backend systems. I want to bring that ownership mindset here and "
        f"help {company} deliver dependable product experiences."
    )


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


def _career_context_blob() -> str:
    profile = load_or_build_profile()
    exp = "\n".join((profile.get("experience_raw") or [])[:40])
    skills = ", ".join((profile.get("skills") or [])[:30])
    facts = "\n".join(f"- {f}" for f in CAREER_FACTS)
    return (
        f"Career facts:\n{facts}\n"
        f"Skills: {skills}\n"
        f"Experience lines (from CV):\n{exp[:3500]}"
    )


def template_relevant_experience(job: JobLike, apply_profile: dict[str, Any]) -> str:
    company = job.company or "this company"
    title = job.title or "Backend Engineer"
    years = apply_profile.get("years_experience") or "4+"
    themes = _jd_theme_hits(job)
    theme_clause = (
        f"This {title} role at {company} calls for {', '.join(themes[:5])}. "
        if themes
        else f"For the {title} role at {company}, "
    )
    return (
        f"{theme_clause}"
        f"I bring {years} years building multi-tenant backends, payment integrations, real-time systems, "
        f"and monitored production services in Node.js/TypeScript. At Metaverse Magna I helped platforms "
        f"support 100k+ unique players daily and about 2M game sessions. At Teamlyf I delivered NestJS SaaS "
        f"foundations and started LangGraph-based memory for projects, documents, and chats so generated "
        f"tasks stay in project context. I design REST APIs, harden auth (JWT/RBAC), and deploy with Docker "
        f"and CI/CD. That maps directly to what this role needs."
    )


def template_application_answers(job: JobLike, apply_profile: dict[str, Any]) -> list[dict[str, str]]:
    company = job.company or "this company"
    title = job.title or "Backend Engineer"
    years = apply_profile.get("years_experience") or "4+"
    return [
        {
            "question": "Tell us about yourself",
            "answer": template_about_yourself(job, apply_profile),
        },
        {
            "question": "Why do you want this role / Why this company?",
            "answer": (
                f"I want the {title} role at {company} because it matches my backend focus: "
                f"scalable APIs, Node.js/TypeScript systems, and high-quality delivery. "
                f"I can contribute quickly and own features from design to production."
            ),
        },
        {
            "question": "Relevant experience / What makes you a fit?",
            "answer": template_relevant_experience(job, apply_profile),
        },
        {
            "question": "Years of experience",
            "answer": f"{years} years of professional backend development experience.",
        },
        {
            "question": "Biggest achievement",
            "answer": (
                "At Metaverse Magna I helped build Game Studio backend systems used to deploy 30+ games, "
                "serve about 2 million game sessions in less than a year, and support over 100k unique "
                "players daily. Earlier I also delivered multi-tenant SaaS backends and secure payment "
                "webhook flows from zero to production."
            ),
        },
        {
            "question": "Work authorization / Location",
            "answer": apply_profile.get("work_authorization")
            or "Eligible to work remotely from Nigeria.",
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


async def _llm_text(prompt: str, fallback: str, max_tokens: int = 1200, creds: LLMCreds | None = None) -> str:
    if not has_llm(creds):
        return fallback
    try:
        text = await llm_complete(
            prompt=prompt,
            json_mode=False,
            max_tokens=max_tokens,
            temperature=0.4,
            creds=creds,
        )
        return (text or "").strip() or fallback
    except Exception:
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
            "Rewrite this cover note from scratch with a fresh angle. Keep facts truthful. "
            "Do not copy the old wording. Improve clarity and company fit.\n"
            f"Previous cover note:\n{previous[:2000]}\n"
        )
    prompt = (
        "Write a short application cover note (ASD-STE100: short active sentences, max 120 words). "
        "No fluff. Truthful. Do not invent employers. "
        "If you mention the resume, say 'my resume' — never 'tailored resume' or 'customized resume'.\n"
        f"{rewrite_bit}"
        f"Candidate: {json.dumps(apply_profile)}\n"
        f"Job title: {job.title}\nCompany: {job.company}\n"
        f"Job snippet: {(job.description or '')[:1500]}\n"
        "Return plain text only."
    )
    return await _llm_text(prompt, fallback, max_tokens=400, creds=creds)


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
        "Produce a fresh rewrite of the answers. Vary wording from a generic template, "
        "but keep the same question themes and truthful facts.\n"
        if rewrite
        else ""
    )
    prompt = (
        "Create short truthful answers for common job application form questions. "
        "Use ASD-STE100: short active sentences. Do not invent employers or skills. "
        "Return ONLY JSON: {\"answers\":[{\"question\":\"...\",\"answer\":\"...\"}]}\n"
        f"{rewrite_bit}"
        "Include these themes: about yourself, why this role/company, relevant experience, "
        "years of experience, biggest achievement, work authorization/location, "
        "salary expectation, earliest start / availability.\n"
        "For 'Tell us about yourself' / about yourself, follow this order of ideas "
        "(structure only — never write the words Present, Past, or Future as labels):\n"
        "1) Current focus at Metaverse Magna and scale impact "
        "(30+ games deployed, ~2M game sessions in under a year, 100k+ unique players daily).\n"
        "2) Brief skills shaped by prior backend/SaaS/real-time work "
        "(include Teamlyf LangGraph memory experiment when AI/context tooling is relevant).\n"
        "3) Connect goals to THIS company and role. Keep it concise, positive, relevant.\n"
        "Write it as natural flowing prose a person would say aloud.\n"
        "For 'Relevant experience / What makes you a fit?':\n"
        "- Read the FULL job description below.\n"
        "- Name 2–4 concrete requirements from the JD.\n"
        "- Map each to specific truthful experience (employer + outcome).\n"
        "- Do not answer with a generic backend blurb that could fit any company.\n"
        "Use these facts when relevant; do not invent bigger numbers.\n"
        f"Candidate profile: {json.dumps(apply_profile)}\n"
        f"{_career_context_blob()}\n"
        f"Job title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"FULL job description:\n{_job_description(job, 4500)}\n"
        "Also provide a strong fallback-quality about-yourself answer if rewriting.\n"
        f"Fallback about-yourself draft:\n{template_about_yourself(job, apply_profile)}\n"
        f"Fallback fit draft:\n{template_relevant_experience(job, apply_profile)}\n"
    )
    raw = await _llm_text(prompt, "", max_tokens=1600, creds=creds)
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
        return cleaned or fallback
    except Exception:
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
    about_rules = ""
    ql = question.lower()
    if "about yourself" in ql:
        about_rules = (
            "Structure the answer as current work → prior experience → interest in this role. "
            "Do NOT write labels like Present:, Past:, or Future: — speak as natural prose. "
            "Include Metaverse Magna impact (30+ games, ~2M sessions, 100k+ unique players daily), "
            "backend/SaaS skills, and a clear link to this company/role.\n"
        )
    elif any(k in ql for k in ("relevant experience", "makes you a fit", "why are you a fit", "why you")):
        about_rules = (
            "Ground the answer in the FULL job description. Pull 2–4 concrete JD requirements "
            "and map each to truthful experience (employer + outcome). Mention Teamlyf LangGraph "
            "memory work when the JD mentions AI/agents/LLM/context/tools. "
            "No generic blurb that could apply to any company.\n"
        )
    prompt = (
        "Rewrite this job-application form answer. Truthful only. Short active sentences. "
        "Do not invent employers or skills. Return plain text answer only (no JSON, no labels).\n"
        f"{about_rules}"
        f"Question: {question}\n"
        f"Previous answer:\n{previous_answer[:2500]}\n"
        f"Candidate profile: {json.dumps(apply_profile)}\n"
        f"{_career_context_blob()}\n"
        f"Job title: {job.title}\nCompany: {job.company}\n"
        f"FULL job description:\n{_job_description(job, 4500)}\n"
    )
    text = await _llm_text(prompt, fallback, max_tokens=700, creds=creds)
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
    user_id: str | None = None,
) -> dict[str, Any]:
    apply_profile = apply_profile or load_apply_profile()
    draft = load_job_draft(job.id, user_id=user_id)
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

    save_job_draft(
        job.id,
        {
            "cover_blurb": cover,
            "answers": answers,
            "job_title": job.title,
            "company": job.company,
        },
        user_id=user_id,
    )
    return {"cover_blurb": cover, "answers": answers}


async def rewrite_answer_in_draft(
    job: JobLike,
    question: str,
    apply_profile: dict[str, Any] | None = None,
    creds: LLMCreds | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    apply_profile = apply_profile or load_apply_profile()
    copy = await ensure_apply_copy(job, apply_profile, creds=creds, user_id=user_id)
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
    save_job_draft(
        job.id,
        {"cover_blurb": copy["cover_blurb"], "answers": answers},
        user_id=user_id,
    )
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
