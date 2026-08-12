from __future__ import annotations

import json
import re
from typing import Any

from app.matching.filters import (
    has_strong_title_signal,
    has_target_stack_signal,
    is_excluded_title,
    is_foreign_stack_title,
    skill_in_text,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def score_job_detail(
    job: dict[str, Any], profile: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, Any]:
    """Full match breakdown for UI + scoring."""
    title = _norm(job.get("title") or "")
    reasons: list[str] = []
    positives: list[dict[str, Any]] = []
    penalties: list[dict[str, Any]] = []
    skill_hits: list[str] = []
    jd_stack: list[str] = []
    foreign_in_title: list[str] = []

    exclude_extra = list((cfg.get("exclude_title_patterns") or []))
    if is_excluded_title(job.get("title") or "", exclude_extra):
        return {
            "score": 5.0,
            "reasons": ["Excluded non-engineering / non-target title"],
            "positives": [],
            "penalties": [{"label": "Excluded title", "delta": -95, "detail": "Title filter blocked this role"}],
            "skill_hits": [],
            "skill_gaps": [],
            "jd_stack": [],
            "foreign_stack": [],
            "connection_summary": "This title is outside your target roles.",
        }

    if is_foreign_stack_title(job.get("title") or ""):
        foreign = _foreign_tokens_in_title(title)
        return {
            "score": 8.0,
            "reasons": [f"Foreign-stack title (not Node/TypeScript): {', '.join(foreign) or 'other language'}"],
            "positives": [],
            "penalties": [
                {
                    "label": "Foreign-stack title",
                    "delta": -90,
                    "detail": "Title centers on a stack outside your primary Node.js/TypeScript focus",
                }
            ],
            "skill_hits": [],
            "skill_gaps": [],
            "jd_stack": foreign,
            "foreign_stack": foreign,
            "connection_summary": "Weak fit: role is labeled for another language stack.",
        }

    # Cap description for ranking CPU / memory (detail UI may still pass full text).
    desc_raw = job.get("description") or ""
    if len(desc_raw) > 6000:
        desc_raw = desc_raw[:6000]
    desc = _norm(desc_raw)
    location = _norm(job.get("location") or "")
    blob = f"{title} {desc}"

    if not has_target_stack_signal(blob):
        return {
            "score": 6.0,
            "reasons": [
                "No TypeScript/Node or Python/FastAPI mention — outside target stack"
            ],
            "positives": [],
            "penalties": [
                {
                    "label": "Missing target stack",
                    "delta": -94,
                    "detail": "Looking for TypeScript/Node/NestJS and/or Python/FastAPI roles",
                }
            ],
            "skill_hits": [],
            "skill_gaps": [],
            "jd_stack": [],
            "foreign_stack": [],
            "connection_summary": "No TypeScript/Node or Python signal — not a target role.",
        }

    score = 0.0

    strong = has_strong_title_signal(job.get("title") or "")
    if not strong:
        if "backend" in blob or "node" in blob or "typescript" in blob:
            score = 35.0
            msg = "Weak title signal; stack mentioned in description only"
            reasons.append(msg)
            positives.append({"label": msg, "delta": 35})
        else:
            return {
                "score": 15.0,
                "reasons": ["Missing backend / software-engineer title signal"],
                "positives": [],
                "penalties": [
                    {"label": "Weak title", "delta": -85, "detail": "No strong backend/software-engineer signal"}
                ],
                "skill_hits": [],
                "skill_gaps": [],
                "jd_stack": [],
                "foreign_stack": [],
                "connection_summary": "Title does not read as a backend/software engineering target.",
            }
    else:
        score = 35.0
        msg = "Strong engineering title signal"
        reasons.append(msg)
        positives.append({"label": msg, "delta": 35})

    title_keywords = [_norm(k) for k in (cfg.get("title_keywords") or [])]
    meaningful = [k for k in title_keywords if k and k not in {"api"} and len(k) >= 3]
    title_hits = [k for k in meaningful if k in title]
    if "api engineer" in title or (re.search(r"\bapi\b", title) and "engineer" in title):
        title_hits.append("api engineer")
    title_hits = list(dict.fromkeys(title_hits))
    if title_hits:
        delta = min(20.0, 6.0 * len(title_hits))
        score += delta
        msg = f"Title keywords: {', '.join(title_hits[:4])}"
        reasons.append(msg)
        positives.append({"label": msg, "delta": delta})

    profile_skills = list(profile.get("skills") or cfg.get("profile_skills") or [])
    seen: set[str] = set()
    for skill in profile_skills:
        if skill_in_text(skill, blob):
            key = skill.lower()
            if key not in seen:
                seen.add(key)
                skill_hits.append(skill)
    if skill_hits:
        delta = min(35.0, 3.5 * len(skill_hits))
        score += delta
        msg = f"Skills overlap ({len(skill_hits)}): {', '.join(skill_hits[:8])}"
        reasons.append(msg)
        positives.append({"label": msg, "delta": delta})

    # Profile skills not evidenced in JD (connection gaps)
    skill_gaps: list[str] = []
    for skill in profile_skills:
        if skill.lower() in seen:
            continue
        # Only show core/tech gaps, not every soft skill
        if any(
            tok in skill.lower()
            for tok in (
                "node",
                "type",
                "nest",
                "express",
                "python",
                "fastapi",
                "mongo",
                "mysql",
                "postgres",
                "redis",
                "docker",
                "aws",
                "websocket",
                "microservice",
            )
        ):
            skill_gaps.append(skill)
    skill_gaps = skill_gaps[:8]

    prefer_remote = bool((cfg.get("search") or {}).get("prefer_remote", True))
    if prefer_remote and ("remote" in location or "remote" in title or "worldwide" in location):
        score += 8.0
        msg = "Remote-friendly location"
        reasons.append(msg)
        positives.append({"label": msg, "delta": 8})
    elif "nigeria" in location or "lagos" in location or "africa" in location:
        score += 8.0
        msg = "Location match (NG/Africa)"
        reasons.append(msg)
        positives.append({"label": msg, "delta": 8})

    early = f"{title} {desc[:900]}"
    for boost, label, pts in [
        (r"\bnode\.?js\b|\bnodejs\b", "Node.js", 4.0),
        (r"\btypescript\b", "TypeScript", 3.0),
        (r"\bnestjs\b", "NestJS", 3.0),
        (r"\bpython\b", "Python", 3.0),
        (r"\bfastapi\b", "FastAPI", 3.0),
        (r"\bmongodb\b", "MongoDB", 2.0),
        (r"\bpostgresql\b|\bpostgres\b", "PostgreSQL", 2.0),
        (r"\bdocker\b", "Docker", 2.0),
        (r"\baws\b", "AWS", 2.0),
        (r"\bpayment|fintech|billing\b", "Payments/fintech", 3.0),
    ]:
        if re.search(boost, early):
            jd_stack.append(label)
            if label.lower() not in " ".join(reasons).lower():
                score += pts
                msg = f"Stack/domain: {label}"
                reasons.append(msg)
                positives.append({"label": msg, "delta": pts})

    penalty_keywords = [_norm(k) for k in (cfg.get("penalty_keywords") or [])]
    primary_stack = bool(
        re.search(
            r"\bnode|\btypescript|\bnestjs|\bexpress\b|\bpython\b|\bfastapi\b",
            title + " " + desc[:500],
        )
    )
    found_penalties: list[str] = []
    for pk in penalty_keywords:
        if not pk:
            continue
        if pk in title or re.search(rf"\b{re.escape(pk)}\b", title):
            found_penalties.append(pk)
        elif pk in desc[:700] and not primary_stack:
            found_penalties.append(pk)

    for heavy in ("rails", "ruby on rails", ".net", "c#", "java", "golang", "go ", "kotlin", "scala", "php"):
        token = heavy.strip()
        if token in title and not re.search(r"\bnode|\btypescript\b", title):
            found_penalties.append(token)
            foreign_in_title.append(token)

    # Broader Go detection (avoid matching "good", "ago")
    if re.search(r"\b(?:golang|go)\b", title) and not re.search(r"\bnode|\btypescript\b", title):
        found_penalties.append("go/golang")
        foreign_in_title.append("go/golang")

    if found_penalties:
        uniq_p = list(dict.fromkeys(found_penalties))
        delta = -min(45.0, 16.0 * len(uniq_p))
        score += delta
        msg = f"Stack mismatch: {', '.join(uniq_p[:4])}"
        reasons.append(msg)
        penalties.append({"label": msg, "delta": delta, "detail": "JD emphasizes stacks outside your Node/TS core"})

    if re.search(r"\bdevops\b|\bsite reliability\b|\bsre\b", title) and not re.search(
        r"\bbackend\b|\bnode|\btypescript\b", title
    ):
        score -= 12.0
        msg = "DevOps-heavy title (lower priority)"
        reasons.append(msg)
        penalties.append({"label": msg, "delta": -12, "detail": "More ops-focused than backend product work"})

    if re.search(r"\bmanager\b|\bdirector\b", title) and not re.search(r"\bengineering manager\b", title):
        score -= 15.0
        msg = "Manager/director title"
        reasons.append(msg)
        penalties.append({"label": msg, "delta": -15, "detail": "Leadership title, not IC backend"})

    # Cap foreign-stack-in-title hard even if skills overlap elsewhere
    if foreign_in_title and not primary_stack:
        score = min(score, 38.0)
        reasons.append("Capped score: foreign-stack title without Node/TS")
        penalties.append(
            {
                "label": "Score cap (foreign-stack title)",
                "delta": 0,
                "detail": "Max ~38% when title is Go/Java/etc. without Node/TypeScript",
            }
        )

    score = max(0.0, min(100.0, round(score, 1)))
    if not reasons:
        reasons.append("Limited keyword overlap with profile")

    if skill_hits and not penalties:
        connection = (
            f"Strong link: JD asks for {', '.join(skill_hits[:5])}, which overlaps your profile."
        )
    elif skill_hits and penalties:
        connection = (
            f"Mixed fit: overlap on {', '.join(skill_hits[:4])}, but downranked for "
            f"{penalties[0]['label']}."
        )
    elif penalties:
        connection = f"Weak link: {penalties[0].get('detail') or penalties[0]['label']}"
    else:
        connection = "Limited direct skill connection — ranking is mostly from title/location signals."

    return {
        "score": score,
        "reasons": reasons,
        "positives": positives,
        "penalties": penalties,
        "skill_hits": skill_hits,
        "skill_gaps": skill_gaps,
        "jd_stack": list(dict.fromkeys(jd_stack + foreign_in_title))[:12],
        "foreign_stack": list(dict.fromkeys(foreign_in_title)),
        "connection_summary": connection,
    }


def score_job(job: dict[str, Any], profile: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, list[str]]:
    """Return score 0-100 and human-readable reasons."""
    detail = score_job_detail(job, profile, cfg)
    return float(detail["score"]), list(detail["reasons"])


def _foreign_tokens_in_title(title: str) -> list[str]:
    found: list[str] = []
    checks = [
        (r"\bgolang\b|\bgo(?:lang)?\b", "Go/Golang"),
        (r"\bjava\b", "Java"),
        (r"\bruby\b|\brails\b", "Ruby/Rails"),
        (r"\b\.net\b|\bc#\b", ".NET/C#"),
        (r"\bkotlin\b", "Kotlin"),
        (r"\bscala\b", "Scala"),
        (r"\bphp\b", "PHP"),
        (r"\brust\b", "Rust"),
        (r"\bc\+\+\b", "C++"),
    ]
    for pat, label in checks:
        if re.search(pat, title) and not re.search(r"\bnode|\btypescript\b", title):
            found.append(label)
    return found


def reasons_to_json(reasons: list[str]) -> str:
    return json.dumps(reasons)


def reasons_from_json(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return [raw] if raw else []
