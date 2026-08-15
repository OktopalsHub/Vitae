"""CV tailor helpers — text processing, rule-based fallback, post-validation.

These are the building blocks used by cv_tailor.py for resume generation
when no LLM is available or for post-processing LLM output.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_DATE_SPAN = re.compile(
    rf"("
    rf"{_MONTH}\s+\d{{4}}\s*[-\u2013\u2014]\s*(?:{_MONTH}\s*[-\u2013\u2014]?\s*\d{{4}}|Present|\d{{4}})"
    rf"|"
    rf"{_MONTH}\s*[-\u2013\u2014]\s*\d{{4}}\s*[-\u2013\u2014]\s*(?:{_MONTH}\s*[-\u2013\u2014]?\s*\d{{4}}|Present|\d{{4}})"
    rf"|"
    rf"\d{{4}}\s*[-\u2013\u2014]\s*(?:Present|\d{{4}})"
    rf")",
    re.I,
)


def dedupe_csv(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip(" ,;")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def split_header(line: str) -> tuple[str, str]:
    """Split 'Company - Role   dates' into title + dates."""
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(rf"({_MONTH})\s*[-\u2013\u2014]\s*(\d{{4}})", r"\1 \2", line, flags=re.I)
    m = _DATE_SPAN.search(line)
    dates = ""
    title = line
    if m:
        dates = re.sub(r"\s*[-\u2013\u2014]\s*", " - ", m.group(0))
        dates = re.sub(r"\s*-\s*-\s*", " - ", dates)
        title = (line[: m.start()] + " " + line[m.end() :]).strip(" -|\t")
    title = re.sub(r"\s{2,}", " ", title).strip(" -|")
    return title, dates


def ste100_bullet(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    replacements = [
        (r"^Architected and delivered\b", "Design and deliver"),
        (r"^Architected and integrated\b", "Add"),
        (r"^Architected\b", "Design"),
        (r"^Engineered\b", "Build"),
        (r"^Developed\b", "Build"),
        (r"^Implemented\b", "Implement"),
        (r"^Maintaining\b", "Maintain"),
        (r"^Enhanced\b", "Improve"),
        (r"^Optimized\b", "Optimize"),
        (r"^Authored and published\b", "Publish"),
        (r"^Authored\b", "Write"),
        (r"^Contributed to defining\b", "Define"),
        (r"^Led the backend development team for\b", "Lead backend development for"),
        (r"^Collaborated with\b", "Work with"),
        (r"^Integrated\b", "Integrate"),
        (r"^Secured\b", "Secure"),
        (r"^Automated\b", "Automate"),
        (r"^Deployed\b", "Deploy"),
    ]
    for pat, rep in replacements:
        t = re.sub(pat, rep, t, count=1, flags=re.I)
    if len(t) > 220:
        t = t[:217].rsplit(" ", 1)[0] + "."
    return t


def is_excluded_bullet(line: str) -> bool:
    return False


def parse_blocks(lines: list[str], role_hints: tuple[str, ...]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        low = line.lower()
        looks_header = _DATE_SPAN.search(line) and any(h in low for h in role_hints)
        if looks_header:
            if current:
                blocks.append(current)
            title, dates = split_header(line)
            current = {"title": title, "dates": dates, "bullets": []}
        elif current and line:
            if is_excluded_bullet(line):
                continue
            current["bullets"].append(line)
    if current:
        blocks.append(current)
    return blocks


# ---------------------------------------------------------------------------
# Rule-based fallback (no LLM)
# ---------------------------------------------------------------------------

def fallback_resume(profile: dict[str, Any], job: Any) -> dict[str, Any]:
    """Rule-based tailor when LLM is unavailable — only uses profile facts."""
    raw_skills = profile.get("skills") or []
    if not isinstance(raw_skills, list):
        raw_skills = []
    skills = [str(s).strip() for s in raw_skills if str(s).strip()]

    def pick(keys: tuple[str, ...]) -> str:
        chosen: list[str] = []
        for s in skills:
            low = s.lower()
            if any(k in low for k in keys):
                parts = re.split(r"[,;/()]| and ", s)
                chosen.extend(p.strip() for p in parts if p.strip())
        return ", ".join(dedupe_csv(chosen)[:8])

    skill_groups = []
    for label, keys in (
        ("Languages & Frameworks", ("node", "type", "javascript", "nest", "express", "python", "fast", "java", "go", "rust", "ruby", "php", "c#", ".net")),
        ("Databases", ("sql", "mongo", "redis", "prisma", "typeorm", "mongoose", "postgres", "mysql", "dynamo", "elastic")),
        ("DevOps & Tools", ("docker", "aws", "git", "ci", "cloud", "digitalocean", "kubernetes", "terraform", "linux")),
        ("APIs & Systems", ("api", "rest", "graphql", "websocket", "grpc", "microservice", "saas", "auth", "queue")),
    ):
        value = pick(keys)
        if value:
            skill_groups.append({"label": label, "value": value})
    bucketed = set()
    for g in skill_groups:
        for part in g["value"].split(","):
            bucketed.add(part.strip().lower())
    leftover = [s for s in skills if s.lower() not in bucketed and not any(s.lower() in b for b in bucketed)]
    if leftover:
        skill_groups.append({"label": "Skills", "value": ", ".join(leftover[:12])})

    experiences = parse_blocks(
        profile.get("experience_raw") or [],
        ("engineer", "developer", "author"),
    )
    for exp in experiences:
        bullets = [ste100_bullet(b) for b in (exp.get("bullets") or [])]
        exp["bullets"] = bullets[:5]
        exp["title"] = re.sub(r"\s*\|?\s*$", "", exp.get("title") or "").strip()

    experiences = [e for e in experiences if e.get("bullets")][:4]

    projects = parse_blocks(
        profile.get("projects_raw") or [],
        ("engineer", "developer", "author", "open source"),
    )
    for proj in projects:
        proj["title"] = re.sub(r"\s*\|?\s*$", "", proj.get("title") or "").strip()
        proj["bullets"] = [ste100_bullet(b) for b in (proj.get("bullets") or []) if not is_excluded_bullet(b)][:2]
    projects = [p for p in projects if p.get("bullets")][:2]

    edu_lines = profile.get("education_raw") or []
    education: list[dict[str, str]] = []
    if isinstance(edu_lines, list) and edu_lines:
        school_line = str(edu_lines[0] or "").strip()
        school, dates = split_header(school_line)
        details = str(edu_lines[1]).strip() if len(edu_lines) > 1 else ""
        if not dates:
            m = re.search(rf"{_MONTH}.+", school_line, re.I)
            if m:
                dates = m.group(0).strip()
                school = school_line[: m.start()].strip()
        if school or details:
            education = [{"school": school or school_line, "dates": dates or "", "details": details}]

    name = str(profile.get("name") or "").strip() or "Candidate"
    profile_summary = str(profile.get("summary") or "").strip()
    company = job.company or "this team"
    title = job.title or "this role"
    skill_preview = ", ".join(skills[:5])
    if profile_summary:
        summary = profile_summary
        if title or company:
            summary = f"{summary.rstrip('.')} Seeking the {title} role at {company}."
    else:
        parts = [f"{name}."]
        if skill_preview:
            parts.append(f"Skills include {skill_preview}.")
        parts.append(f"Applying for {title} at {company}.")
        summary = " ".join(parts)

    return {
        "summary": summary,
        "skills": skill_groups,
        "experiences": experiences,
        "projects": projects,
        "education": education,
    }


# ---------------------------------------------------------------------------
# Post-validation
# ---------------------------------------------------------------------------

def post_validate_resume(data: dict[str, Any], job: Any) -> dict[str, Any]:
    """Normalize LLM or fallback output."""
    for exp in data.get("experiences") or []:
        exp["bullets"] = [
            b for b in (exp.get("bullets") or []) if not is_excluded_bullet(b)
        ]
    for proj in data.get("projects") or []:
        proj["bullets"] = [
            b for b in (proj.get("bullets") or []) if not is_excluded_bullet(b)
        ]
    for sk in data.get("skills") or []:
        if isinstance(sk.get("value"), str):
            parts = [p.strip() for p in sk["value"].split(",")]
            sk["value"] = ", ".join(dedupe_csv(parts))
    for exp in data.get("experiences") or []:
        title, dates = exp.get("title") or "", exp.get("dates") or ""
        if not dates and _DATE_SPAN.search(title):
            t2, d2 = split_header(title)
            exp["title"], exp["dates"] = t2, d2
        exp["title"] = re.sub(r"\s*\|\s*$", "", exp.get("title") or "").strip()
        exp["dates"] = re.sub(r"\s*-\s*-\s*", " - ", exp.get("dates") or "").strip(" |")
    return data


def extract_json(text: str) -> dict[str, Any]:
    import json
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)
