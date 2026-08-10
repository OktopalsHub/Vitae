from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from docx import Document

from app.config import load_yaml_config, project_path


EXCLUDED_DEFAULT = [
    "GeoIP",
    "Politically Exposed",
    "PEP (",
    "age verification",
    "KYC-linked",
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_master_cv_path() -> Path:
    """Legacy single-file CV for local smoke scripts only — not used in B2C uploads."""
    cfg = load_yaml_config()
    name = (cfg.get("master_cv") or "").strip()
    if not name:
        raise FileNotFoundError(
            "No master_cv configured. Upload a CV in the app (per-user), "
            "or set master_cv in config.yaml for local smoke scripts only."
        )
    path = project_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Master CV not found: {path}")
    return path


def extract_docx_paragraphs(path: Path) -> list[str]:
    doc = Document(str(path))
    return [_clean(p.text) for p in doc.paragraphs if _clean(p.text)]


def _is_excluded(line: str, patterns: list[str]) -> bool:
    lower = line.lower()
    for pat in patterns:
        if pat.lower() in lower:
            return True
    return False


def extract_pdf_paragraphs(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    chunks: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for line in text.splitlines():
            cleaned = _clean(line)
            if cleaned:
                chunks.append(cleaned)
    return chunks


def parse_profile(paragraphs: list[str] | None = None, *, source_path: Path | None = None) -> dict[str, Any]:
    cfg = load_yaml_config()
    exclude = list(cfg.get("exclude_bullet_patterns") or EXCLUDED_DEFAULT)
    skills_cfg = list(cfg.get("profile_skills") or [])

    if paragraphs is None:
        paragraphs = extract_docx_paragraphs(load_master_cv_path())
        source_path = source_path or load_master_cv_path()

    name = paragraphs[0] if paragraphs else "Candidate"
    contact = paragraphs[1] if len(paragraphs) > 1 else ""

    sections: dict[str, list[str]] = {}
    current = "header"
    known_heads = {
        "professional summary",
        "summary",
        "education",
        "skills",
        "professional experience",
        "experience",
        "volunteer/personal projects",
        "projects",
        "personal projects",
    }

    for line in paragraphs[2:]:
        key = line.lower().strip(":")
        if key in known_heads:
            current = key
            sections.setdefault(current, [])
            continue
        if _is_excluded(line, exclude):
            continue
        sections.setdefault(current, []).append(line)

    summary = " ".join(sections.get("professional summary") or sections.get("summary") or [])

    # Skills from labeled lines + config
    skill_lines = sections.get("skills") or []
    skills: list[str] = []
    for line in skill_lines:
        if ":" in line:
            _, rhs = line.split(":", 1)
            parts = re.split(r"[,;/]", rhs)
        else:
            parts = re.split(r"[,;/]", line)
        for p in parts:
            item = _clean(p)
            if item and item.lower() not in {s.lower() for s in skills}:
                skills.append(item)
    for s in skills_cfg:
        if s.lower() not in {x.lower() for x in skills}:
            skills.append(s)

    experience_raw = sections.get("professional experience") or sections.get("experience") or []
    projects_raw = (
        sections.get("volunteer/personal projects")
        or sections.get("projects")
        or sections.get("personal projects")
        or []
    )
    education_raw = sections.get("education") or []

    profile = {
        "name": name,
        "contact": contact,
        "summary": summary,
        "skills": skills,
        "skill_lines": skill_lines,
        "experience_raw": experience_raw,
        "projects_raw": projects_raw,
        "education_raw": education_raw,
        "full_text": "\n".join(paragraphs),
        "master_cv": str(source_path) if source_path else "",
    }
    return profile


def parse_docx_profile(path: Path) -> dict[str, Any]:
    paragraphs = extract_docx_paragraphs(path)
    return parse_profile(paragraphs, source_path=path)


def parse_pdf_profile(path: Path) -> dict[str, Any]:
    paragraphs = extract_pdf_paragraphs(path)
    return parse_profile(paragraphs, source_path=path)


def parse_cv_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf_profile(path)
    if suffix == ".docx":
        return parse_docx_profile(path)
    raise ValueError("Unsupported CV format (use PDF or DOCX)")


def contact_parts_from_profile(profile: dict[str, Any]) -> dict[str, str]:
    """Best-effort contact chips from parsed name/contact line."""
    contact = str(profile.get("contact") or "")
    email = ""
    phone = ""
    linkedin = ""
    github = ""
    website = ""
    for part in re.split(r"[|]", contact):
        p = part.strip()
        pl = p.lower()
        if "@" in p and not email:
            email = p.replace(" ", "")
        elif re.search(r"\+?\d[\d\s-]{7,}", p) and not phone:
            phone = re.sub(r"\s+", "", p)
        elif "linkedin" in pl and not linkedin:
            linkedin = p if p.startswith("http") else f"https://{p}"
        elif "github" in pl and not github:
            github = p if p.startswith("http") else f"https://{p}"
        elif pl.startswith("http") and not website:
            website = p
    return {
        "full_name": str(profile.get("name") or ""),
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "github": github,
        "website": website,
    }


def save_profile_cache(profile: dict[str, Any]) -> Path:
    out = project_path("data", "profile", "profile.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return out


def load_or_build_profile(force: bool = False) -> dict[str, Any]:
    cache = project_path("data", "profile", "profile.json")
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    profile = parse_profile()
    save_profile_cache(profile)
    return profile
